# The data doesn't add up: reconciling 52K vs 45K vs 57K signups

```bash
pip install -r requirements.txt      # pandas only
python generate_data.py              # writes data/ (three messy CSV extracts + ground truth)
python reconcile.py                  # prints the report, writes output/*.csv (~3 seconds)
```

The full printed report from the last run is in [output/report.txt](output/report.txt). Every number below comes
from it. The data is synthetic, but each issue was injected by `generate_data.py`, and section 7 of the report
checks the script recovers every one: 15/15 injected issues at the exact count, and the final number
lands 18 below the true 50,000, exactly the 18 users the generator dropped from both A and C.

## 1. TL;DR

**Recommended number: 49,982 (≈50.0K) new signups on 2026-10-07.**
Definition: *unique users whose account was created on 2026-10-07 UTC, excluding test/internal accounts and a
bot burst.*

**Why the systems differ:** they don't measure the same thing. C (57.0K) logs signup *attempts*, with
duplicates. A (51.8K) logs *created accounts*, with a double-loaded batch, test accounts and bots. B (45.1K)
counts *identity-verified* users on a *US/Pacific* day. Once all three are normalized, deduplicated and put on
the same day boundary, they nest like a funnel at 99%+.

## 2. Approach (order matters)

1. **Normalize.** User IDs are trimmed, lower-cased, stripped of B's `USR-` prefix and of C's `12345678.0`
   float artifacts. Timestamps go to UTC. This alone changes 8% of A's IDs, 32% of C's and 100% of B's.
   Without it, those users look like mismatches.
2. **Dedupe all three, not just C.** Comparing a clean C with raw A and B would be unfair. A and B have
   duplicates too. Duplicates come in two kinds because they have different owners:
   *exact repeats* (same ID, same timestamp: a loading bug) and *retries* (same ID, different timestamp:
   double taps and re-submits, an app issue). The keeper is the first *completed* attempt.
3. **Compare who, not just how many.** Set overlaps turn "B is 7K lower" into a list of specific users.
4. **Test the funnel-stage hypothesis with containment** instead of asserting it.
5. **Profile the mismatches** by hour, platform, email domain, status and ingestion lag.
6. **Bridge** from the largest raw count to the recommended number, one cause per row.

## 3. Findings

**Duplicates exist in every system**:

| System | Raw rows | Exact repeats | (of which only visible after normalizing) | Retries | Unique users |
|---|---|---|---|---|---|
| A | 51,800 | 419, **100% from signups in the 13:00–13:15 window** | 249 | 400 | 50,981 |
| B | 45,106 | 300 | 93 | 0 | 44,806 |
| C | 57,012 | 1,200 | 563 | 1,800 | 54,012 |

All of A's exact repeats come from a single 15-minute batch, which points to a loader re-run. 47% of exact
repeats overall would have been missed by deduplicating on raw values.

**The funnel-stage hypothesis holds**, once the timezone is fixed:

| Containment | Result |
|---|---|
| B ⊆ A, B as extracted | **70.7%**: looks like B has thousands of users A doesn't |
| B ⊆ A, B restricted to the UTC day | **99.1%** |
| A ⊆ C (deduped) | **99.1%** |
| C users not in A that are incomplete or bots | 85.2% |

B's timestamps have no offset. For the 31,688 users in both A and B, B's timestamp, read as UTC, is exactly
−7.00h from A's for 100% of them. That is US/Pacific daylight time. B's "day" therefore runs 07:00→07:00 UTC,
and 12,841 of B's users signed up on the *next* UTC day.

**Bridge from C's 57K to one number** ([output/bridge.csv](output/bridge.csv)):

| Step | Users | Running total |
|---|---|---|
| System C raw rows (signup attempts) | 57,012 | 57,012 |
| − exact repeats (same user, same timestamp) | −1,200 | 55,812 |
| − retries (same user, different timestamp) | −1,800 | 54,012 |
| − test / internal accounts | −598 | 53,414 |
| − bot burst | −900 | 52,514 |
| − abandoned / incomplete signups | −2,783 | 49,731 |
| ± day boundary: completed outside the UTC day | −216 | 49,515 |
| **= C, cleaned** | | **49,515** |
| + accounts in A (system of record) missing from C | +467 | 49,982 |
| **= Recommended** | | **49,982** |

The same cleaning gives 49,691 from A and 49,515 from C, a 0.4% difference. The recommended number is their
union.

## 4. Root causes and what I'd flag

| Discrepancy | Cause | Evidence | Owner |
|---|---|---|---|
| C counts **events**, A and B count **users** | C logs every attempt | 3,000 duplicate rows. 2,783 users never completed. C users not in A are 85% incomplete or bots | Product analytics |
| A double-counts one batch | loader re-run | 419 exact repeats, all from one 15-min window | Data engineering |
| B is a different stage *and* a different day | KYC report, Pacific timezone | −7.00h offset. 99.1% containment after alignment. 91% of A's users are in B, flat across hours | KYC integration / BI |
| **Data still arriving in A** | extract pulled at 00:02, before the last batch landed | after 23:42 UTC, C has 824 completed signups and A only 591 | Data engineering: re-pull |
| Test accounts in every count | no `is_internal` flag at source | 598 / 590 / 147 in C / A / B (`@test.com`, `@example.com`, company domain, `qa+`) | Engineering / QA |
| Bots | 900 signups in a 6-minute burst on 3 disposable domains | 700 created accounts (in A). **0 reached B**: KYC stopped them | Fraud / Trust & Safety |
| C misses ~0.9% of accounts | client-side tracking | 467 users in A but not C. 30% signed up after 22:00 (mobile events queued past midnight). Web is 32.8% of them vs 24.9% baseline (ad blockers) | Instrumentation |
| C's day is by ingestion time | partitioned on load date | 216 completed signups from 10-06 in the 10-07 extract | Product analytics |
| **No written definition of "a signup" and no designated source of truth** | each team built its own dashboard | this exercise | Growth + Data leadership |

The last row is the real root cause. Each system is behaving correctly *by its own definition*.

**Bot rule.** A signup is flagged as a bot if it falls in a minute with more than 3× the median per-minute
volume *and* uses an email domain with at least 80% of its signups inside bursts. *Rejected:* flagging
disposable domains outright, because that would also remove 22 real users who used those domains outside the
burst.

**Judgment calls:**
- **Account created, not attempt or KYC.** "New signups" in an onboarding deck means accounts. Attempts
  overstate by about 7K. KYC belongs one step later in the funnel.
- **Union of A and C, not A alone.** A is the system of record but hadn't finished loading. The C-only
  late-evening signups are what A will contain after the re-pull.
- **UTC day**, not Pacific. I assumed UTC is the company reporting standard. It's flagged as an open item.
- **Bots excluded even though they created accounts.** The deck measures acquisition of real users.
  Fraud should confirm.

## 5. Investigation plan beyond the CSVs

For each system owner, I'd ask the lineage questions:
- **What table or event stream feeds the dashboard, and what event or row is "a signup"?**
  (Attempt, account row, or KYC pass?)
- **Which timestamp, which timezone, and is the day partitioned by event time or load time?**
- **Are test, internal and fraud accounts filtered, and where?** Is there an `is_internal` flag?
- **When is the day complete?** What's the load schedule, the SLA, and is backfill expected?
- **Is the dashboard counting rows or distinct users?**

In the source tables, I'd verify:
- A's load log for the 13:00–13:15 batch (re-run?), and A's row count after the next load.
- C's 10-08 partition for the ~140 late-evening users missing from C.
- The client SDK's known drop rate on web.
- The vendor console's timezone setting.
- Fraud's view of the 03:41 UTC burst.

## 6. Message to the PM (sent at the ~1-hour mark, not Friday)

> **Signups for Oct 7: ~50.0K (49,982).** Definition: unique new accounts created that day (UTC), excluding
> test/internal accounts and a bot burst.
>
> **Confidence:** high on the definition, ±0.5% on the number. The accounts table and the analytics events
> independently agree within 0.4% once cleaned.
>
> **Why the dashboards differ:** they measure different steps. C (57K) counts signup *attempts*, including
> retries and abandoned signups. B (45K) counts users who *passed ID verification*, on a Pacific-time day.
> Together they form a mini-funnel: of the ~35K accounts created 07:00–24:00 UTC, 91% had verified their
> identity by the next morning.
>
> **Still open:** (1) re-pulling the accounts table tomorrow morning, because the last batch was still loading
> (expect no change; it's already covered); (2) Fraud to confirm 700 bot accounts should be excluded;
> (3) confirming UTC is our reporting day.
>
> **If someone asks "why does dashboard B say 45K?":** *"B counts users who finished ID verification, on a
> Pacific-time day. It's a later funnel step, not a different count of the same thing."*

## 7. Prevention

- **One written definition** ("a signup = a unique account created, by UTC day, excluding `is_internal` and
  fraud-flagged users") and **one source-of-truth table**. Every dashboard reads from that table or is
  labeled as a different metric ("signup attempts", "ID-verified users").
- That table is the `signed_up_at` / `signup_cohort_week` grain of a modeled onboarding funnel. My
  [funnel data model](../README.md) builds exactly that: deterministic identity resolution, UTC
  normalization with stated assumptions, strict vs. raw step flags, and tests for uniqueness, freshness and
  volume anomalies. Those tests would have caught A's double load and its late batch before anyone pulled a
  number.

## 8. Time budget (2 hours)

| Time | Work |
|---|---|
| 0:00–0:30 | Script: normalize, dedupe, overlaps, containment |
| 0:30–1:15 | Investigation: profiles, timezone check, bridge. **Holding message to the PM at ~1:00** |
| 1:15–1:45 | Write-up and final PM message |
| 1:45–2:00 | Buffer: re-run, sanity-check the bridge sums |

**What's explained.** Every user in the 57.0K → 50.0K bridge is attributed to a cause the data shows. One
piece rests on a hypothesis rather than proof: about 330 accounts in A but missing from C with no
late-evening explanation. That's 0.7% of the number. I attribute it to client-side tracking loss, consistent
with its web skew, and would confirm it with the SDK team. It doesn't move the recommended number, because
those users are in A, the system of record.
