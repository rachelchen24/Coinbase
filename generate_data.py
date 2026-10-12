"""
Generate ~5,000 users of deliberately messy onboarding data across three source systems.

Outputs (all values written as text, like a raw landing zone):
  seeds/app_events.csv, seeds/kyc_vendor_events.csv, seeds/accounts.csv, seeds/ledger_events.csv
  seeds/ground_truth/pending_steps.csv  (users whose next step happens after AS_OF; unknowable in real life)
  seeds/manifest.json                    (what was injected + oracle counts computed in Python, independently
                                          of the SQL, so run.py can check the models and tests catch it all)
"""
from __future__ import annotations

import csv
import json
import math
import random
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
SEED = 20261001
N_USERS = 5000
LAUNCH_AT = datetime(2026, 6, 1, tzinfo=UTC)
AS_OF = datetime(2026, 10, 1, tzinfo=UTC)          # the "now" of the dataset; makes every run reproducible
KYC_SOURCE_TZ = "America/New_York"                  # vendor console setting; naive timestamps are local to it
OUTAGE_DAY = date(2026, 8, 19)                      # app release that silently stopped emitting email_verified
OUTAGE_DROP_RATE = 0.95
KYC_FEED_STALE_HOURS = 30                           # vendor export job stopped 30h before AS_OF
LATE_AFTER = timedelta(hours=72)                    # definition of "late-arriving" shared with the SQL

ROOT = Path(__file__).parent
SEEDS = ROOT / "seeds"

rng = random.Random(SEED)


# ---------------------------------------------------------------- helpers
def lag(median_hours: float, sigma: float) -> timedelta:
    """Log-normal delay: most users are quick, a long tail is slow."""
    return timedelta(hours=median_hours * math.exp(rng.gauss(0, sigma)))


def uniform(lo: timedelta, hi: timedelta) -> timedelta:
    return lo + (hi - lo) * rng.random()


def ny_offset(dt: datetime) -> timedelta:
    """US Eastern offset for 2026 (hard-coded DST bounds avoid a tzdata dependency on Windows)."""
    dst = datetime(2026, 3, 8, 7, tzinfo=UTC) <= dt < datetime(2026, 11, 1, 6, tzinfo=UTC)
    return timedelta(hours=-4 if dst else -5)


def iso_mixed(dt: datetime) -> str:
    """ISO-8601 with whichever offset the emitting client happened to use."""
    offset = rng.choice([0, 0, 0, -4, -7, 1, 5.5])
    local = dt.astimezone(timezone(timedelta(hours=offset)))
    s = local.isoformat(timespec=rng.choice(["seconds", "milliseconds"]))
    return s.replace("+00:00", "Z") if offset == 0 and rng.random() < 0.7 else s


def iso_z(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def naive_ny(dt: datetime) -> str:
    return (dt + ny_offset(dt)).strftime("%Y-%m-%d %H:%M:%S")


def epoch_mixed(dt: datetime) -> str:
    """The ledger emits seconds from one service and milliseconds from another."""
    ts = dt.timestamp()
    return str(int(ts * 1000)) if rng.random() < 0.35 else str(int(ts))


def messy_email(email: str) -> str:
    variants = [email, email.upper(), email.capitalize(), f"  {email}", f"{email} ",
                email.split("@")[0] + "@" + email.split("@")[1].upper()]
    return rng.choice(variants)


def sample_signup() -> datetime:
    # density grows linearly ~1x -> 1.6x over the window (a growing product); inverse-CDF sampling
    a, u = 0.6, rng.random()
    x = (-1 + math.sqrt(1 + 2 * a * u * (1 + a / 2))) / a
    return LAUNCH_AT + (AS_OF - LAUNCH_AT - timedelta(hours=1)) * x


# ---------------------------------------------------------------- 1. simulate true user behaviour
counter = defaultdict(int)


def next_id(prefix: str, width: int) -> str:
    counter[prefix] += 1
    return f"{prefix}{counter[prefix]:0{width}d}"


users = []
for i, signed_up in enumerate(sorted(sample_signup() for _ in range(N_USERS))):
    uid = 100001 + i
    u = dict(user_id=uid, signup_at=signed_up, email=f"user{uid}@example.com", email_events=[],
             kyc_checks=[], approved_at=None, account_id=None, transfers=[], trades=[])
    users.append(u)

    if rng.random() < 0.82:
        t = signed_up + lag(0.3, 1.6)
        u["email_events"].append(t)
        if rng.random() < 0.08:                                     # user clicks the link twice
            u["email_events"].append(t + lag(3, 1.0))
        if rng.random() < 0.78:
            t += lag(2, 1.3)
            for attempt in range(3):
                approved = rng.random() < (0.80, 0.62, 0.50)[attempt]
                u["kyc_checks"].append([t, "approved" if approved else "declined"])
                if approved:
                    u["approved_at"] = t
                    break
                if rng.random() > 0.6:                              # gives up after a decline
                    break
                t += lag(20, 1.0)

    if not u["approved_at"]:
        continue
    u["account_id"] = str(7000000 + i)
    u["account_opened_at"] = u["approved_at"] + uniform(timedelta(minutes=1), timedelta(minutes=10))

    # friction: users who needed KYC retries are less likely to fund
    if rng.random() >= (0.70, 0.52, 0.38)[len(u["kyc_checks"]) - 1]:
        continue

    def transfer(initiated: datetime, returned: bool) -> dict:
        settled = initiated + uniform(timedelta(days=1), timedelta(days=3))
        tr = dict(transfer_id=next_id("trf_", 7), initiated=initiated, settled=settled,
                  returned=settled + uniform(timedelta(days=2), timedelta(days=5)) if returned else None,
                  amount=rng.choice([25, 50, 100, 250, 500, 1000, 2500]))
        u["transfers"].append(tr)
        return tr

    t = u["account_opened_at"] + lag(18, 1.3)
    first = transfer(t, returned=rng.random() < 0.06)
    success_at = None
    if first["returned"] is None:
        success_at = t
    elif rng.random() < 0.4:
        # traded on instant buying power, then the ACH came back; no retry
        u["trades"].append(t + uniform(timedelta(minutes=30), first["returned"] - t))
    elif rng.random() < 0.5:
        success_at = first["returned"] + lag(24, 1.0)
        transfer(success_at, returned=False)

    if success_at and rng.random() < 0.15:
        transfer(success_at + lag(240, 0.8), returned=False)
    if success_at and rng.random() < 0.72:
        t = success_at + lag(30, 1.4)
        u["trades"].append(t)
        while rng.random() < 0.55:
            t += lag(72, 1.2)
            u["trades"].append(t)


def first_success_transfer(u):
    ok = [tr for tr in u["transfers"] if tr["returned"] is None]
    return min(ok, key=lambda tr: tr["initiated"]) if ok else None


# ---------------------------------------------------------------- 2. per-user injections
kyc_users = [u for u in users if u["kyc_checks"]]
for u in kyc_users:
    u["applicant_id"] = "apl_" + "".join(rng.choice("0123456789abcdef") for _ in range(10))
    # 12% of applicants came through a legacy SDK that never passed our user_id to the vendor
    u["has_vendor_ref"] = rng.random() >= 0.12
    # a quarter of those gave the vendor a different email than at sign-up -> unmatchable
    u["kyc_email"] = u["email"] if u["has_vendor_ref"] or rng.random() >= 0.25 else f"user{u['user_id']}.alt@example.net"

# out-of-order: vendor reports the *latest review* time, which for re-reviewed applicants lands after funding
ooo_pool = [u for u in kyc_users if u["has_vendor_ref"] and first_success_transfer(u)
            and first_success_transfer(u)["initiated"] < AS_OF - timedelta(days=6)]
ooo_users = rng.sample(ooo_pool, 60)
for u in ooo_users:
    new_at = first_success_transfer(u)["initiated"] + uniform(timedelta(hours=2), timedelta(hours=36))
    u["kyc_checks"][-1][0] = new_at
    u["approved_at"] = new_at
    u["out_of_order"] = True

# two accounts, one email: same person signed up twice -> email can't identify them deterministically
dup_pool = [u for u in kyc_users if not u.get("out_of_order")]
dup_email_users = rng.sample(dup_pool, 8)
for u in dup_email_users:
    other = rng.choice([o for o in users if o["user_id"] < u["user_id"]])
    u["email"] = u["kyc_email"] = other["email"]
    u["has_vendor_ref"] = False

# vendor default '1970-01-01' on some declined attempts of later-approved users (pre-launch timestamps)
prelaunch_pool = [u for u in kyc_users if len(u["kyc_checks"]) >= 2 and u["approved_at"]
                  and u["has_vendor_ref"] and not u.get("out_of_order")]
for u in rng.sample(prelaunch_pool, 3):
    u["kyc_checks"][0].append("prelaunch")
# device clock a year ahead on some *repeat* email_verified events (future-dated timestamps)
future_pool = [u for u in users if len(u["email_events"]) == 2 and u["email_events"][1].date() != OUTAGE_DAY
               and u["email_events"][1] < AS_OF - timedelta(days=2)]
future_users = rng.sample(future_pool, 3)


# ---------------------------------------------------------------- 3. render source rows
# Each row keeps private fields: _true (when it really happened), _loaded (warehouse load time),
# _reported (the UTC instant its timestamp string actually encodes), _user (ground-truth owner).
def loaded_at(true_at: datetime, normal: tuple, late_p: float, late: tuple) -> datetime:
    if rng.random() < late_p:
        return true_at + uniform(timedelta(days=late[0]), timedelta(days=late[1]))
    return true_at + uniform(timedelta(minutes=normal[0]), timedelta(minutes=normal[1]))


app_rows, kyc_rows, account_rows, ledger_rows = [], [], [], []
outage_dropped_events = 0

for u in users:
    t = u["signup_at"]
    app_rows.append(dict(event_id=next_id("evt_", 8), user_id=str(u["user_id"]), event_name="sign_up",
                         event_ts=iso_mixed(t), email=messy_email(u["email"]),
                         _true=t, _reported=t, _user=u["user_id"],
                         _loaded=loaded_at(t, (0.5, 15), 0.0, (0, 0))))   # sign-up is written server-side
    for k, t in enumerate(u["email_events"]):
        if t.date() == OUTAGE_DAY and rng.random() < OUTAGE_DROP_RATE:
            outage_dropped_events += 1
            continue
        reported = t + timedelta(days=365, hours=3) if (k == 1 and u in future_users) else t
        app_rows.append(dict(event_id=next_id("evt_", 8), user_id=str(u["user_id"]), event_name="email_verified",
                             event_ts=iso_mixed(reported), email="",
                             _true=t, _reported=reported, _user=u["user_id"],
                             _loaded=loaded_at(t, (0.5, 15), 0.005, (4, 7))))

    for check in u["kyc_checks"]:
        t, status = check[0], check[1]
        prelaunch = len(check) > 2
        kyc_rows.append(dict(check_id=next_id("chk_", 7), applicant_id=u["applicant_id"],
                             external_ref=str(u["user_id"]) if u["has_vendor_ref"] else "",
                             email=messy_email(u["kyc_email"]), check_status=status,
                             created_at="1970-01-01 00:00:00" if prelaunch else naive_ny(t),
                             _true=t, _reported=datetime(1970, 1, 1, 5, tzinfo=UTC) if prelaunch else t,
                             _user=u["user_id"], _loaded=loaded_at(t, (5, 240), 0.04, (3.5, 14))))

    if u["account_id"]:
        t = u["account_opened_at"]
        account_rows.append(dict(account_id=u["account_id"], user_id=str(u["user_id"]), opened_at=iso_z(t),
                                 _true=t, _loaded=t + timedelta(minutes=20)))
    for tr in u["transfers"]:
        for status in ("initiated", "settled", "returned"):
            t = tr[status]
            if t is None:
                continue
            ledger_rows.append(dict(ledger_event_id=next_id("led_", 8), account_id=u["account_id"],
                                    event_type="deposit", reference_id=tr["transfer_id"], status=status,
                                    amount_usd=f"{tr['amount']:.2f}", event_epoch=epoch_mixed(t),
                                    _true=t, _reported=t, _user=u["user_id"],
                                    _loaded=loaded_at(t, (1, 45), 0.01, (4, 10))))
    for t in u["trades"]:
        ledger_rows.append(dict(ledger_event_id=next_id("led_", 8), account_id=u["account_id"], event_type="trade",
                                reference_id=next_id("trd_", 7), status="filled",
                                amount_usd=f"{rng.choice([10, 25, 50, 100, 200]):.2f}", event_epoch=epoch_mixed(t),
                                _true=t, _reported=t, _user=u["user_id"],
                                _loaded=loaded_at(t, (1, 45), 0.01, (4, 10))))

# KYC records from the vendor's QA sandbox that leaked into the production export (no such user)
for n in range(6):
    t = datetime(2026, 9, 3, tzinfo=UTC) + timedelta(days=n, hours=rng.randint(13, 20))
    kyc_rows.append(dict(check_id=next_id("chk_", 7), applicant_id=f"apl_qa{n:08d}", external_ref="",
                         email=f" QA+{n}@KYC-Sandbox.example ", check_status="approved", created_at=naive_ny(t),
                         _true=t, _reported=t, _user=None, _loaded=t + timedelta(hours=1)))

# internal test accounts in the ledger that never went through app sign-up
for n in range(4):
    acct, t = str(7900001 + n), datetime(2026, 9, 2 + 2 * n, 15, tzinfo=UTC)
    account_rows.append(dict(account_id=acct, user_id=str(900001 + n), opened_at=iso_z(t),
                             _true=t, _loaded=t + timedelta(minutes=20)))
    trf = next_id("trf_", 7)
    for status, dt in (("initiated", t + timedelta(hours=1)), ("settled", t + timedelta(days=1)),
                       ("trade", t + timedelta(days=1, hours=2))):
        ledger_rows.append(dict(ledger_event_id=next_id("led_", 8), account_id=acct,
                                event_type="trade" if status == "trade" else "deposit",
                                reference_id=next_id("trd_", 7) if status == "trade" else trf,
                                status="filled" if status == "trade" else status, amount_usd="1.00",
                                event_epoch=epoch_mixed(dt), _true=dt, _reported=dt, _user=None,
                                _loaded=dt + timedelta(minutes=5)))


def finalize(rows: list, dup_rate: float, load_cutoff: datetime) -> tuple[list, int]:
    """Add at-least-once redeliveries, then keep only what had happened and been loaded by the cutoff."""
    out = []
    for r in rows:
        out.append(r)
        if rng.random() < dup_rate:
            out.append({**r, "_loaded": r["_loaded"] + uniform(timedelta(minutes=1), timedelta(hours=6))})
    out = [r for r in out if r["_true"] <= AS_OF and r["_loaded"] <= load_cutoff]
    rng.shuffle(out)                                   # extracts arrive unordered
    return out, len(out)


app_rows, _ = finalize(app_rows, 0.02, AS_OF)
kyc_rows, _ = finalize(kyc_rows, 0.03, AS_OF - timedelta(hours=KYC_FEED_STALE_HOURS))
ledger_rows, _ = finalize(ledger_rows, 0.015, AS_OF)
account_rows = [r for r in account_rows if r["_loaded"] <= AS_OF]


def write(name: str, rows: list, cols: list[str], loaded_col="_loaded_at"):
    path = SEEDS / f"{name}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols + [loaded_col])
        for r in rows:
            w.writerow([r[c] for c in cols] + [iso_z(r["_loaded"])])


SEEDS.mkdir(exist_ok=True)
(SEEDS / "ground_truth").mkdir(exist_ok=True)
write("app_events", app_rows, ["event_id", "user_id", "event_name", "event_ts", "email"])
write("kyc_vendor_events", kyc_rows, ["check_id", "applicant_id", "external_ref", "email", "check_status", "created_at"])
write("accounts", account_rows, ["account_id", "user_id", "opened_at"])
write("ledger_events", ledger_rows, ["ledger_event_id", "account_id", "event_type", "reference_id", "status",
                                     "amount_usd", "event_epoch"])

# ---------------------------------------------------------------- 4. oracle: expected outcomes, in plain Python
def first_copies(rows, key):
    seen = {}
    for r in sorted(rows, key=lambda r: r["_loaded"]):
        seen.setdefault(r[key], r)
    return list(seen.values())


def plausible(r):
    return LAUNCH_AT <= r["_reported"] <= AS_OF


app_u, kyc_u, led_u = (first_copies(app_rows, "event_id"), first_copies(kyc_rows, "check_id"),
                       first_copies(ledger_rows, "ledger_event_id"))

# identity resolution, re-derived from the rows themselves
signup_email = defaultdict(set)
for r in app_u:
    if r["event_name"] == "sign_up":
        signup_email[r["email"].strip().lower()].add(r["_user"])
known_users = {u["user_id"] for u in users}
applicants = defaultdict(lambda: dict(refs=set(), emails=set(), rows=0))
for r in kyc_u:
    a = applicants[r["applicant_id"]]
    a["rows"] += 1
    a["emails"].add(r["email"].strip().lower())
    if r["external_ref"]:
        a["refs"].add(int(r["external_ref"]))
unresolved_reasons = defaultdict(int)
resolved_applicants = set()
for aid, a in applicants.items():
    if len(a["refs"]) == 1 and next(iter(a["refs"])) in known_users:
        resolved_applicants.add(aid)
    elif len(a["refs"]) > 1:
        unresolved_reasons["conflicting_vendor_reference"] += 1
    else:
        matches = set().union(*(signup_email.get(e, set()) for e in a["emails"]))
        if len(matches) == 1:
            resolved_applicants.add(aid)
        else:
            unresolved_reasons["ambiguous_email" if len(matches) > 1 else "no_match"] += 1
acct_user = {r["account_id"]: int(r["user_id"]) for r in account_rows}
orphan_accounts = {a for a in {r["account_id"] for r in led_u} if acct_user.get(a) not in known_users}

# per-user visible steps (resolved, plausible), mirroring the documented funnel definitions
vis = defaultdict(lambda: dict(email=[], kyc_any=0, kyc_ok=[], dep_any=0, dep_ok=[], dep_returned=0, trade=[]))
for r in app_u:
    if r["event_name"] == "email_verified" and plausible(r):
        vis[r["_user"]]["email"].append(r["_reported"])
for r in kyc_u:
    if r["applicant_id"] in resolved_applicants and plausible(r):
        v = vis[r["_user"]]
        v["kyc_any"] += 1
        if r["check_status"] == "approved":
            v["kyc_ok"].append(r["_reported"])
transfers = defaultdict(dict)
for r in led_u:
    if r["account_id"] in orphan_accounts:
        continue
    if r["event_type"] == "trade":
        vis[r["_user"]]["trade"].append(r["_reported"])
    else:
        transfers[(r["_user"], r["reference_id"])][r["status"]] = r["_reported"]
for (user, _), st in transfers.items():
    v = vis[user]
    v["dep_any"] += 1
    if "returned" in st:
        v["dep_returned"] += 1
    elif "settled" in st:
        v["dep_ok"].append(st.get("initiated", st["settled"]))

expected = defaultdict(int)
for u in users:
    v = vis[u["user_id"]]
    ts = [u["signup_at"], min(v["email"], default=None), min(v["kyc_ok"], default=None),
          min(v["dep_ok"], default=None), min(v["trade"], default=None)]
    later = lambda k: any(t is not None for t in ts[k + 1:])
    expected["users_inferred_email_verified"] += (not v["email"]) and later(1)
    expected["users_inferred_id_verified"] += (v["kyc_any"] == 0) and later(2)
    expected["users_inferred_first_deposit"] += (v["dep_any"] == 0) and later(3)
    expected["users_only_returned_deposits"] += v["dep_returned"] > 0 and not v["dep_ok"]
    running = None
    for t in ts:
        if t is None:
            continue
        if running and t < running:
            expected["users_out_of_sequence"] += 1
            break
        running = max(running or t, t)
expected["returned_transfers"] = sum(1 for st in transfers.values() if "returned" in st)


def late_count(rows):
    return sum(1 for r in rows if plausible(r) and r["_loaded"] - r["_reported"] > LATE_AFTER)


# ground truth the warehouse can never see: whose next step simply hasn't happened yet
pending = []
for u in users:
    ok = first_success_transfer(u)
    steps = [("email_verified", min(u["email_events"], default=None)), ("id_verified", u["approved_at"]),
             ("first_deposit", ok["settled"] if ok else None), ("first_trade", min(u["trades"], default=None))]
    for step, t in steps:
        if t is None:
            break
        if t > AS_OF:
            pending.append((u["user_id"], step))
            break
with (SEEDS / "ground_truth" / "pending_steps.csv").open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["user_id", "pending_step"])
    w.writerows(pending)

manifest = {
    "config": {"seed": SEED, "n_users": N_USERS, "launch_at": iso_z(LAUNCH_AT), "as_of": iso_z(AS_OF),
               "kyc_source_timezone": KYC_SOURCE_TZ, "outage_day": OUTAGE_DAY.isoformat(),
               "kyc_feed_stale_hours": KYC_FEED_STALE_HOURS},
    "injected": {
        "duplicate_rows": {"app_events": len(app_rows) - len(app_u), "kyc_vendor_events": len(kyc_rows) - len(kyc_u),
                           "ledger_events": len(ledger_rows) - len(led_u)},
        "unresolved_kyc_applicants": dict(unresolved_reasons),
        "unresolved_ledger_accounts": len(orphan_accounts),
        "tracker_outage": {"day": OUTAGE_DAY.isoformat(), "step": "email_verification",
                           "events_dropped": outage_dropped_events},
        "out_of_order_users_injected": len(ooo_users),
        "late_arriving_rows": {"app_events": late_count(app_u), "kyc_vendor_events": late_count(kyc_u),
                               "ledger_events": late_count(led_u)},
        "future_dated_rows": sum(1 for r in app_u + kyc_u + led_u if r["_reported"] > AS_OF),
        "pre_launch_rows": sum(1 for r in app_u + kyc_u + led_u if r["_reported"] < LAUNCH_AT),
        "kyc_feed_stale_hours": KYC_FEED_STALE_HOURS,
        "users_with_pending_next_step": len(pending),
    },
    "expected": dict(expected),
    "source_rows": {"app_events": len(app_rows), "kyc_vendor_events": len(kyc_rows),
                    "accounts": len(account_rows), "ledger_events": len(ledger_rows)},
}
(SEEDS / "manifest.json").write_text(json.dumps(manifest, indent=2))

if __name__ == "__main__":
    print(json.dumps(manifest["injected"], indent=2))
    print(json.dumps(manifest["expected"], indent=2))
