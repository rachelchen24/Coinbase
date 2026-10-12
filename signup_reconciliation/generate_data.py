"""
Generate three messy "daily new signups" extracts for 2026-10-07 that disagree the way real systems do.

    System A  accounts service table: one row per created account, UTC, loaded in 15-minute batches
    System B  KYC vendor report: users who passed identity verification, timestamps in US/Pacific
    System C  client-side product analytics: one row per signup *attempt*, UTC, partitioned by ingestion time

Writes data/system_{a,b,c}.csv and data/ground_truth.json (what was injected; unknowable in real life,
used only by reconcile.py's final self-check).
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

UTC = timezone.utc
rng = random.Random(20261007)

DAY = datetime(2026, 10, 7, tzinfo=UTC)
NEXT = DAY + timedelta(days=1)
PDT = timedelta(hours=-7)                       # US/Pacific offset in October (daylight time)
A_PULLED_AT = NEXT + timedelta(minutes=3)       # A extract pulled before its last batches landed
B_PULLED_AT = NEXT + timedelta(hours=10)        # B extract pulled the next morning (Pacific day + 3h)
N_COMPLETED_PER_DAY = 50_000                    # real accounts created per day (the truth we want back)
ABANDON_RATIO = 0.056                           # abandoned starters per completed signup
N_TEST, N_TEST_IN_B = 600, 150
N_BOTS, N_BOTS_COMPLETING = 900, 700
BOT_BURST_START = DAY + timedelta(hours=3, minutes=41)
DUPES = {"A": {"retry": 400}, "B": {"exact": 300}, "C": {"exact": 1_200, "retry": 1_800}}

# signups by UTC hour: US-centric, quiet 07:00-12:00 UTC (US night)
HOUR_W = [7, 6.5, 6, 5, 4, 3, 2, 1.5, 1, 1, 1, 1.5, 2.5, 4, 5.5, 6.5, 7, 7, 7, 7, 7, 7, 7, 7]
REAL_DOMAINS = ["gmail.com"] * 10 + ["yahoo.com"] * 3 + ["outlook.com"] * 3 + ["icloud.com"] * 3 + \
               ["hotmail.com", "proton.me", "aol.com"]
DISPOSABLE = ["quickmail.xyz", "inboxnow.top", "tmpbox.click"]
PLATFORMS = ["ios"] * 40 + ["android"] * 35 + ["web"] * 25
SOURCES = ["organic"] * 40 + ["paid_social"] * 25 + ["search"] * 15 + ["referral"] * 12 + ["direct"] * 8

OUT = Path(__file__).parent / "data"
_ids: set[int] = set()


def new_id() -> int:
    while (i := rng.randint(10_000_000, 99_999_999)) in _ids:
        pass
    _ids.add(i)
    return i


def draw_ts(day: datetime, hours: range) -> datetime:
    h = rng.choices(list(hours), weights=[HOUR_W[x] for x in hours])[0]
    return day + timedelta(hours=h, seconds=rng.uniform(0, 3600))


def person(ts: datetime, status: str, kind: str = "real", **kw) -> dict:
    uid = new_id()
    p = {"user_id": uid, "ts": ts, "status": status, "kind": kind,
         "platform": rng.choice(PLATFORMS), "source": rng.choice(SOURCES),
         "email": f"user{uid}@{rng.choice(REAL_DOMAINS)}"}
    p.update(kw)
    return p


def window_count(n_per_day: float, hours: range) -> int:
    return round(n_per_day * sum(HOUR_W[h] for h in hours) / sum(HOUR_W))


# ------------------------------------------------------------------ population
people: list[dict] = []
for day, hours in ((DAY - timedelta(days=1), range(12, 24)), (DAY, range(24)), (NEXT, range(0, 7))):
    for _ in range(window_count(N_COMPLETED_PER_DAY, hours)):
        people.append(person(draw_ts(day, hours), "completed"))
    for _ in range(window_count(N_COMPLETED_PER_DAY * ABANDON_RATIO, hours)):
        p = person(draw_ts(day, hours), "abandoned")
        if rng.random() < 0.3:
            p["email"] = None                   # left before entering an email
        people.append(p)

# a few real people use disposable domains too, so "disposable domain" alone can't be the bot rule
for p in rng.sample([p for p in people if p["status"] == "completed"], 40):
    p["email"] = f"user{p['user_id']}@{rng.choice(DISPOSABLE)}"

test_people = []
for n in range(N_TEST):
    email = rng.choice([f"qa+{n}@acmebrokerage.com", f"tester{n}@test.com", f"loadtest{n}@example.com"])
    test_people.append(person(draw_ts(DAY, range(15, 24)), "completed", kind="test", email=email,
                              source="direct"))
people += test_people

bots = []
for n in range(N_BOTS):
    ts = BOT_BURST_START + timedelta(seconds=rng.uniform(0, 6 * 60))
    status = "completed" if n < N_BOTS_COMPLETING else "abandoned"
    bots.append(person(ts, status, kind="bot", platform="web", source="direct",
                       email=f"{''.join(rng.choices('abcdefghijklmnop', k=7))}{n}@{rng.choice(DISPOSABLE)}"))
people += bots

# ------------------------------------------------------------------ System C: client analytics, by ingestion date
rows_c, c_lost = [], 0
for p in people:
    if p["kind"] == "real" and rng.random() < (0.012 if p["platform"] == "web" else 0.003):
        c_lost += p["ts"].date() == DAY.date() and p["status"] == "completed"
        continue                                # ad blockers / killed app: the event never arrives
    if p["platform"] != "web" and rng.random() < 0.04:
        lag = timedelta(minutes=90 * math.exp(rng.gauss(0, 1)))   # offline queue, flushed on next app open
    else:
        lag = timedelta(seconds=rng.uniform(0.5, 20))
    ingested = p["ts"] + lag
    if ingested.date() == DAY.date():
        rows_c.append({**p, "ingested_at": ingested})

# ------------------------------------------------------------------ System A: accounts table, UTC day, batch loads
rows_a = []
for p in people:
    if p["status"] != "completed" or p["ts"].date() != DAY.date():
        continue
    batch_end = p["ts"].replace(minute=p["ts"].minute // 15 * 15, second=0, microsecond=0) + timedelta(minutes=15)
    ingested = batch_end + timedelta(minutes=rng.uniform(1, 4))
    if rng.random() < 0.02:
        ingested += timedelta(minutes=45 * math.exp(rng.gauss(0, 0.8)))
    if ingested <= A_PULLED_AT:
        rows_a.append({**p, "ingested_at": ingested})

# ------------------------------------------------------------------ System B: KYC vendor, Pacific day
rows_b, n_test_b = [], 0
b_start, b_end = DAY - PDT, NEXT - PDT          # Pacific midnight to midnight = 07:00 to 07:00 UTC
for p in people:
    if p["status"] != "completed" or not (b_start <= p["ts"] < b_end) or p["kind"] == "bot":
        continue
    if p["kind"] == "test":
        if n_test_b >= N_TEST_IN_B:
            continue
        n_test_b += 1
    if p["kind"] == "real" and rng.random() > 0.915:
        continue                                # never passes KYC
    verified = p["ts"] + timedelta(minutes=25 * math.exp(rng.gauss(0, 1.6)))
    ingested = verified + timedelta(minutes=rng.uniform(1, 6))
    if ingested <= B_PULLED_AT:
        rows_b.append({**p, "verified_at": verified, "ingested_at": ingested})

# ------------------------------------------------------------------ duplicates
def add_exact(rows: list[dict], n: int) -> None:
    rows += [dict(r) for r in rng.sample(rows, n)]


def add_retry(rows: list[dict], n: int, abandoned_attempts: bool) -> None:
    for r in rng.sample([r for r in rows if r["kind"] != "bot"], n):
        d = dict(r)
        if abandoned_attempts and rng.random() < 0.5:
            d["ts"] = r["ts"] - timedelta(seconds=rng.uniform(30, 600))   # earlier abandoned attempt
            d["status"] = "abandoned"
        else:
            d["ts"] = r["ts"] + timedelta(seconds=rng.uniform(1, 3))      # double tap
        d["ingested_at"] = r["ingested_at"] + (d["ts"] - r["ts"])
        rows.append(d)


# A: the 13:00-13:15 batch was loaded twice (exact repeats share one reload time)
reloaded = [dict(r, ingested_at=r["ingested_at"] + timedelta(hours=1)) for r in rows_a
            if DAY + timedelta(hours=13) <= r["ts"] < DAY + timedelta(hours=13, minutes=15)]
gt_dupes = {"A": {"exact": len(reloaded), "retry": DUPES["A"]["retry"]},
            "B": {"exact": DUPES["B"]["exact"], "retry": 0},
            "C": DUPES["C"]}
add_retry(rows_a, DUPES["A"]["retry"], abandoned_attempts=False)    # double submits
rows_a += reloaded
add_exact(rows_b, DUPES["B"]["exact"])
add_exact(rows_c, DUPES["C"]["exact"])
add_retry(rows_c, DUPES["C"]["retry"], abandoned_attempts=True)


# ------------------------------------------------------------------ render with each system's quirks
def iso_z(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + rng.choice(["Z", "+00:00"])


def naive(dt: datetime, offset: timedelta = timedelta(0)) -> str:
    return (dt + offset).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def fmt_id(uid: int, system: str) -> str:
    if system == "A":
        return f"  {uid}" if rng.random() < 0.08 else str(uid)
    if system == "B":
        return ("usr-" if rng.random() < 0.2 else "USR-") + str(uid)
    s = f"{uid}.0" if rng.random() < 0.3 else str(uid)         # column passed through a float upstream
    return f" {s} " if rng.random() < 0.03 else s


def fmt_email(e: str | None) -> str | None:
    if e is None:
        return None
    return e.upper() if rng.random() < 0.05 else (f" {e}" if rng.random() < 0.03 else e)


frames = {
    "a": pd.DataFrame([{"user_id": fmt_id(r["user_id"], "A"), "created_at": iso_z(r["ts"]),
                        "email": fmt_email(r["email"]), "platform": r["platform"],
                        "signup_source": r["source"], "ingested_at": iso_z(r["ingested_at"])} for r in rows_a]),
    "b": pd.DataFrame([{"user_id": fmt_id(r["user_id"], "B"), "signup_ts": naive(r["ts"], PDT),
                        "email": fmt_email(r["email"]), "kyc_status": "approved",
                        "verified_at": naive(r["verified_at"], PDT),
                        "ingested_at": naive(r["ingested_at"], PDT)} for r in rows_b]),
    "c": pd.DataFrame([{"user_id": fmt_id(r["user_id"], "C"), "event_ts": naive(r["ts"]),
                        "email": fmt_email(r["email"]), "platform": r["platform"], "utm_source": r["source"],
                        "status": r["status"], "ingested_at": naive(r["ingested_at"])} for r in rows_c]),
}

OUT.mkdir(exist_ok=True)
for name, df in frames.items():
    df.sample(frac=1, random_state=rng.randint(0, 10**6)).to_csv(OUT / f"system_{name}.csv", index=False)


# ------------------------------------------------------------------ ground truth
def uniq(rows, pred) -> int:
    return len({r["user_id"] for r in rows if pred(r)})


on_day = lambda r: r["ts"].date() == DAY.date()
real_completed_today = {p["user_id"] for p in people if p["kind"] == "real" and p["status"] == "completed"
                        and on_day(p)}
c_ids, a_ids = {r["user_id"] for r in rows_c}, {r["user_id"] for r in rows_a}
c_completed = {r["user_id"] for r in rows_c if r["status"] == "completed"}
truth = {
    "report_day": DAY.date().isoformat(),
    "raw_rows": {k.upper(): len(v) for k, v in frames.items()},
    "duplicate_rows": gt_dupes,
    "test_users": {"A": uniq(rows_a, lambda r: r["kind"] == "test"), "B": uniq(rows_b, lambda r: r["kind"] == "test"),
                   "C": uniq(rows_c, lambda r: r["kind"] == "test")},
    "bot_users": {"A": uniq(rows_a, lambda r: r["kind"] == "bot"), "B": 0,
                  "C": uniq(rows_c, lambda r: r["kind"] == "bot")},
    "c_abandoned_real_users": len({r["user_id"] for r in rows_c if r["kind"] == "real"} - c_completed),
    "c_completed_real_users_outside_day": len({p["user_id"] for p in people if p["kind"] == "real"
                                               and not on_day(p)} & c_completed),
    "b_users_outside_utc_day": uniq(rows_b, lambda r: not on_day(r)),
    "b_timezone": "America/Los_Angeles (UTC-7 in October)",
    "a_missing_late_arriving": len(real_completed_today - a_ids),
    "c_missing_real_completed": len(real_completed_today - c_ids),
    "c_missing_lost_client_side": c_lost,
    "missing_from_both_a_and_c": len(real_completed_today - a_ids - c_ids),
    "true_new_signups": len(real_completed_today),
}
(OUT / "ground_truth.json").write_text(json.dumps(truth, indent=2))

if __name__ in ("__main__", "generate"):
    print("Generated extracts for", truth["report_day"])
    for k, v in truth["raw_rows"].items():
        print(f"  System {k}: {v:,} rows")
    print("Injected (ground truth):")
    for k, v in truth.items():
        if k not in ("raw_rows", "report_day"):
            print(f"  {k}: {v}")
