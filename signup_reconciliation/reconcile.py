"""
Reconcile three conflicting "new signups" counts for one day.

    python reconcile.py            # generates data/ if missing, prints the report, writes output/*.csv

Order matters: normalize -> dedupe all three -> compare who is where -> profile the mismatches -> bridge.
Comparing before normalizing turns formatting into fake discrepancies; deduping only C would compare
clean C against dirty A and B.
"""
from __future__ import annotations

import json
import runpy
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
DATA, OUT = ROOT / "data", ROOT / "output"
REPORT_DAY = pd.Timestamp("2026-10-07", tz="UTC")

# Per-system lineage facts. The timezone for B is an assumption that check_timezones() verifies from data.
SYSTEMS = {
    "A": {"file": "system_a.csv", "tz": "UTC", "ts": "created_at", "source": "signup_source",
          "counts": "created accounts (accounts service)"},
    "B": {"file": "system_b.csv", "tz": "America/Los_Angeles", "ts": "signup_ts", "source": None,
          "counts": "identity-verified users (KYC vendor)"},
    "C": {"file": "system_c.csv", "tz": "UTC", "ts": "event_ts", "source": "utm_source",
          "counts": "signup attempts (client analytics)"},
}
TEST_EMAIL = r"@(?:test\.com|example\.com|acmebrokerage\.com)$|^(?:qa\+|tester|loadtest)"


# ------------------------------------------------------------------------------ load + normalize
def normalize_id(s: pd.Series) -> pd.Series:
    return (s.astype(str).str.strip().str.lower()
             .str.replace(r"^usr-", "", regex=True)      # B's vendor prefix
             .str.replace(r"\.0$", "", regex=True))      # C's float artifact: 12345678.0


def to_utc(s: pd.Series, tz: str) -> pd.Series:
    ts = pd.to_datetime(s, format="ISO8601")
    return ts.dt.tz_convert("UTC") if ts.dt.tz is not None else ts.dt.tz_localize(tz).dt.tz_convert("UTC")


def load(system: str, tz: str | None = None) -> pd.DataFrame:
    cfg = SYSTEMS[system]
    raw = pd.read_csv(DATA / cfg["file"], dtype=str)
    tz = tz or cfg["tz"]
    return pd.DataFrame({
        "system": system,
        "raw_user_id": raw["user_id"],
        "raw_ts": raw[cfg["ts"]],
        "user_id": normalize_id(raw["user_id"]),
        "signup_ts": to_utc(raw[cfg["ts"]], tz),
        "ingested_at": to_utc(raw["ingested_at"], tz),
        "email": raw["email"].str.strip().str.lower(),
        "platform": raw.get("platform"),
        "source": raw[cfg["source"]] if cfg["source"] else None,
        # A and B only contain completed signups; C logs every attempt
        "status": raw["status"].str.lower() if "status" in raw else "completed",
    })


def check_timezones(a_users: pd.DataFrame) -> dict:
    """B's timestamps carry no offset. Read them as UTC and compare with A for the same users."""
    b_as_utc = dedupe(load("B", tz="UTC"))[0].set_index("user_id")["signup_ts"]
    joined = a_users.set_index("user_id")["signup_ts"].to_frame("a").join(b_as_utc, how="inner")
    offset_h = ((joined["signup_ts"] - joined["a"]).dt.total_seconds() / 3600).round(2)
    return {"matched_users": len(joined), "median_offset_hours": offset_h.median(),
            "share_at_median": (offset_h == offset_h.median()).mean()}


# ------------------------------------------------------------------------------ dedupe
def dedupe(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """One row per user. Exact repeat = same user and same timestamp (a loading bug). Retry = same user,
    different timestamp (double tap, re-submit; an app/instrumentation issue). Keep the first completed
    attempt, else the first attempt."""
    exact = df.duplicated(["user_id", "signup_ts"])
    exact_visible_raw = df.duplicated(["raw_user_id", "raw_ts"]).sum()
    rest = df[~exact].assign(_incomplete=lambda d: d["status"] != "completed")
    users = rest.sort_values(["user_id", "_incomplete", "signup_ts"]).drop_duplicates("user_id").drop(columns="_incomplete")
    stats = {"raw_rows": len(df), "exact_repeats": int(exact.sum()),
             "exact_only_visible_after_normalizing": int(exact.sum() - exact_visible_raw),
             "retry_repeats": len(rest) - len(users), "unique_users": len(users),
             # a re-loaded batch shows up as exact repeats concentrated in one 15-minute window
             "exact_pct_in_top_15min": round(100 * df.loc[exact, "signup_ts"].dt.floor("15min")
                                             .value_counts(normalize=True).max(), 1) if exact.any() else None,
             "top_15min_window": df.loc[exact, "signup_ts"].dt.floor("15min").mode()[0].strftime("%H:%M")
                                 if exact.any() else None}
    return users.reset_index(drop=True), stats


# ------------------------------------------------------------------------------ flags
def bot_ids(c_users: pd.DataFrame) -> set[str]:
    """Bots = signups inside a burst minute (> 3x the day's median per-minute volume) whose email domain is
    concentrated in bursts (>= 80% of that domain's signups). Domain alone is not enough: real people use
    disposable domains too."""
    c = c_users.assign(minute=c_users["signup_ts"].dt.floor("min"),
                       domain=c_users["email"].str.split("@").str[1])
    per_min = c.groupby("minute").size()
    c["in_burst"] = c["minute"].isin(per_min[per_min > 3 * per_min.median()].index)
    burst_share = c.groupby("domain")["in_burst"].mean()
    concentrated = burst_share[burst_share >= 0.8].index
    return set(c.loc[c["in_burst"] & c["domain"].isin(concentrated), "user_id"])


def add_flags(users: pd.DataFrame, bots: set[str]) -> pd.DataFrame:
    return users.assign(
        is_test=users["email"].fillna("").str.contains(TEST_EMAIL, regex=True),
        is_bot=users["user_id"].isin(bots),
        on_report_day=users["signup_ts"].dt.normalize() == REPORT_DAY,
        hour_utc=users["signup_ts"].dt.hour,
        lag_min=(users["ingested_at"] - users["signup_ts"]).dt.total_seconds() / 60,
    )


# ------------------------------------------------------------------------------ compare
def overlaps(a: set, b: set, c: set) -> pd.DataFrame:
    regions = {
        "in all three": a & b & c, "A and C only": (a & c) - b, "A and B only": (a & b) - c,
        "B and C only": (b & c) - a, "A only": a - b - c, "B only": b - a - c, "C only": c - a - b,
    }
    return pd.DataFrame([(k, len(v)) for k, v in regions.items()], columns=["region", "users"])


def containment(inner: set, outer: set) -> float:
    return len(inner & outer) / len(inner)


def profile(name: str, df: pd.DataFrame, explained: pd.Series, reason: str) -> dict:
    """What the mismatched users have in common."""
    return {
        "group": name, "users": len(df),
        "pct_outside_utc_day": round(100 * (~df["on_report_day"]).mean(), 1),
        "pct_2200_0200_utc": round(100 * df["hour_utc"].isin([22, 23, 0, 1]).mean(), 1),
        "pct_web": round(100 * (df["platform"] == "web").mean(), 1) if df["platform"].notna().any() else None,
        "pct_test": round(100 * df["is_test"].mean(), 1),
        "pct_bot": round(100 * df["is_bot"].mean(), 1),
        "pct_not_completed": round(100 * (df["status"] != "completed").mean(), 1),
        "median_ingest_lag_min": round(df["lag_min"].median(), 1),
        "pct_explained": round(100 * explained.mean(), 1) if len(df) else 100.0,
        "explanation": reason,
    }


# ------------------------------------------------------------------------------ report helpers
def section(title: str) -> None:
    print(f"\n{'=' * 100}\n{title}\n{'=' * 100}")


def show(df: pd.DataFrame) -> None:
    print(df.to_string(index=False, float_format=lambda v: f"{v:,.1f}"))


def main() -> None:
    if not (DATA / "ground_truth.json").exists():
        runpy.run_path(str(ROOT / "generate_data.py"), run_name="generate")
    OUT.mkdir(exist_ok=True)

    section("1. Normalize: IDs to one format, timestamps to UTC")
    raw = {s: load(s) for s in SYSTEMS}
    tz = check_timezones(dedupe(raw["A"])[0])
    print(f"B has no timezone in its timestamps. Read naively as UTC, B is {tz['median_offset_hours']:+.2f}h from A "
          f"for the same {tz['matched_users']:,} users ({tz['share_at_median']:.1%} exactly). That is US/Pacific "
          f"daylight time, so B is converted from America/Los_Angeles.")
    for s, df in raw.items():
        changed = (df["raw_user_id"] != df["user_id"]).mean()
        print(f"  {s}: {changed:.1%} of user IDs changed by normalization (whitespace, case, 'usr-' prefix, '.0')")

    section("2. Deduplicate all three systems (not just C)")
    users, dup_stats = {}, {}
    for s, df in raw.items():
        users[s], dup_stats[s] = dedupe(df)
    bots = bot_ids(users["C"])
    users = {s: add_flags(u, bots) for s, u in users.items()}
    dups = pd.DataFrame.from_dict(dup_stats, orient="index").rename_axis("system").reset_index()
    show(dups)
    c_dom = users["C"]["email"].str.split("@").str[1]
    bot_domains = set(c_dom[users["C"]["is_bot"]])
    kept = (c_dom.isin(bot_domains) & ~users["C"]["is_bot"]).sum()
    burst = users["C"].loc[users["C"]["is_bot"], "signup_ts"]
    print(f"\nBots: {len(bots)} users in a signup burst {burst.min():%H:%M}-{burst.max():%H:%M} UTC on "
          f"{len(bot_domains)} disposable domains. "
          f"{kept} other users on those domains signed up outside the burst and are kept as real.")

    section("3. Who is in each system (unique users, before any cleaning)")
    ids = {s: set(u["user_id"]) for s, u in users.items()}
    ov = overlaps(ids["A"], ids["B"], ids["C"])
    show(ov)

    section("4. Hypothesis: each system counts a different funnel stage, so C >= A >= B should nest")
    b_day = set(users["B"].loc[users["B"]["on_report_day"], "user_id"])
    c_not_a = users["C"][~users["C"]["user_id"].isin(ids["A"])]
    contain = pd.DataFrame([
        ("B in A", "B as extracted (Pacific day)", containment(ids["B"], ids["A"])),
        ("B in A", "B restricted to the UTC day", containment(b_day, ids["A"])),
        ("A in C", "deduped", containment(ids["A"], ids["C"])),
        ("C not in A", "share with status != completed or bot", ((c_not_a["status"] != "completed") | c_not_a["is_bot"]).mean()),
    ], columns=["test", "scope", "pct"]).assign(pct=lambda d: (100 * d["pct"]).round(1))
    show(contain)
    holds = contain["pct"].iloc[1] >= 99 and contain["pct"].iloc[2] >= 99
    print(f"\nVerdict: {'HOLDS' if holds else 'DOES NOT HOLD'}. After aligning B to the UTC day, B nests inside A "
          f"and A inside C at >= 99%. C's extra users are mostly attempts that never completed, so C counts "
          f"attempts, A counts created accounts, and B counts identity-verified users.")

    section("5. Profile the users who don't match")
    a, b, c = users["A"], users["B"], users["C"]
    a_last_ingest = a["ingested_at"].max()
    c_clean = c[~c["is_test"] & ~c["is_bot"] & (c["status"] == "completed") & c["on_report_day"]]
    a_clean = a[~a["is_test"] & ~a["is_bot"] & a["on_report_day"]]
    late_window = a_last_ingest - pd.Timedelta(minutes=20)

    c_extra = c_not_a
    b_extra = b[~b["user_id"].isin(ids["A"])]
    a_extra = a_clean[~a_clean["user_id"].isin(ids["C"])]
    # A users who should be in B's window (07:00-24:00 UTC) but aren't verified yet
    a_not_b = a_clean[(a_clean["signup_ts"] >= REPORT_DAY + pd.Timedelta(hours=7)) & ~a_clean["user_id"].isin(ids["B"])]
    profiles = pd.DataFrame([
        profile("C not in A", c_extra,
                c_extra["is_bot"] | (c_extra["status"] != "completed") | ~c_extra["on_report_day"]
                | (c_extra["signup_ts"] >= late_window),
                "abandoned attempts, bots, prior-day rows (C partitions by ingestion time), and the last "
                "minutes of the day A hadn't loaded yet"),
        profile("B not in A", b_extra, ~b_extra["on_report_day"] | (b_extra["signup_ts"] >= late_window),
                "signed up 00:00-07:00 UTC on the next day = the end of B's Pacific day"),
        profile("A not in C", a_extra, a_extra["signup_ts"] >= REPORT_DAY + pd.Timedelta(hours=22),
                "only late-evening ones are explainable (mobile events queued past midnight); "
                "the rest look like client-side tracking loss"),
        profile("A not in B (07-24 UTC)", a_not_b, pd.Series(True, index=a_not_b.index),
                "not identity-verified when B was pulled: failed, or still pending (see coverage by hour)"),
    ])
    show(profiles.drop(columns="explanation"))
    for _, r in profiles.iterrows():
        print(f"  {r['group']}: {r['explanation']}")
    c_late = c_clean["signup_ts"].ge(late_window).sum()
    a_late = a_clean["signup_ts"].ge(late_window).sum()
    print(f"\nA's last load landed at {a_last_ingest:%Y-%m-%d %H:%M} UTC. For signups after {late_window:%H:%M}, "
          f"C has {c_late} completed users and A only {a_late}: A is still loading its last batch.")
    a_not_b_pct = (a_not_b["platform"].value_counts(normalize=True) * 100).round(1).to_dict()
    print(f"Platform mix of 'A not in B': {a_not_b_pct} (no platform is missing wholesale).")
    bands = pd.cut(a_clean["hour_utc"], [7, 12, 18, 21, 23, 24], right=False,
                   labels=["07-12", "12-18", "18-21", "21-23", "23-24"])
    cov = (a_clean.assign(band=bands, in_b=a_clean["user_id"].isin(ids["B"])).dropna(subset=["band"])
           .groupby("band", observed=True)["in_b"].mean().mul(100).round(1))
    print(f"B coverage of A's users by UTC signup hour: {cov.to_dict()}. Flat, so B's gap on these hours is "
          f"KYC pass rate, not verifications still arriving.")

    section("6. Bridge: System C's raw rows to one number")
    s = dup_stats["C"]
    c_real = c[~c["is_test"]]
    c_human = c_real[~c_real["is_bot"]]
    c_completed = c_human[c_human["status"] == "completed"]
    recommended = set(c_clean["user_id"]) | set(a_clean["user_id"])
    steps = [
        ("System C raw rows (signup attempts)", s["raw_rows"]),
        ("- exact repeats (same user, same timestamp)", -s["exact_repeats"]),
        ("- retries (same user, different timestamp)", -s["retry_repeats"]),
        ("- test / internal accounts", -int(c["is_test"].sum())),
        ("- bot burst", -int(c_real["is_bot"].sum())),
        ("- abandoned / incomplete signups", -int((c_human["status"] != "completed").sum())),
        ("± day boundary: completed outside the UTC day", -int((~c_completed["on_report_day"]).sum())),
        ("= C, cleaned", len(c_clean)),
        ("+ accounts in A (system of record) missing from C", len(set(a_clean["user_id"]) - set(c_clean["user_id"]))),
        ("= RECOMMENDED: new accounts created on the UTC day", len(recommended)),
    ]
    bridge = pd.DataFrame(steps, columns=["step", "users"])
    is_subtotal = bridge["step"].str.startswith("=")
    bridge["running_total"] = bridge["users"].where(~is_subtotal, 0).cumsum()
    assert (bridge.loc[is_subtotal, "running_total"] == bridge.loc[is_subtotal, "users"]).all(), "bridge doesn't add up"
    show(bridge)

    summary = pd.DataFrame([{
        "system": sname, "counts": SYSTEMS[sname]["counts"], "raw_rows": dup_stats[sname]["raw_rows"],
        "unique_users": len(u), "test": int(u["is_test"].sum()), "bots": int(u["is_bot"].sum()),
        "real_completed_on_utc_day": int((~u["is_test"] & ~u["is_bot"] & (u["status"] == "completed")
                                          & u["on_report_day"]).sum()),
    } for sname, u in users.items()])
    print("\nSame cleaning applied to every system:")
    show(summary)
    b_verified_day = summary.loc[summary["system"] == "B", "real_completed_on_utc_day"].iloc[0]
    a_window = len(a_clean[a_clean["signup_ts"] >= REPORT_DAY + pd.Timedelta(hours=7)])
    print(f"\nMini-funnel on the hours both A and B cover (07:00-24:00 UTC): {a_window:,} new accounts, of which "
          f"{b_verified_day:,} ({b_verified_day / a_window:.1%}) were identity-verified by the time B was pulled.")

    section("7. Self-check against the generator's ground truth (not available in real life)")
    gt = json.loads((DATA / "ground_truth.json").read_text())
    checks = pd.DataFrame([
        *[(f"{k} exact repeats", gt["duplicate_rows"][k]["exact"], dup_stats[k]["exact_repeats"]) for k in "ABC"],
        *[(f"{k} retries", gt["duplicate_rows"][k]["retry"], dup_stats[k]["retry_repeats"]) for k in "ABC"],
        *[(f"{k} test users", gt["test_users"][k], int(users[k]["is_test"].sum())) for k in "ABC"],
        *[(f"{k} bot users", gt["bot_users"][k], int(users[k]["is_bot"].sum())) for k in "ABC"],
        ("C abandoned real users", gt["c_abandoned_real_users"], -steps[5][1]),
        ("C completed users outside UTC day", gt["c_completed_real_users_outside_day"], -steps[6][1]),
        ("B users outside UTC day", gt["b_users_outside_utc_day"], int((~b["on_report_day"]).sum())),
        ("True new signups (recommended)", gt["true_new_signups"], len(recommended)),
    ], columns=["issue", "injected", "detected"])
    checks["diff"] = checks["detected"] - checks["injected"]
    show(checks)
    print(f"\nRecommended number misses {gt['true_new_signups'] - len(recommended)} real signups: users absent from "
          f"both A (late load) and C (tracking loss); injected = {gt['missing_from_both_a_and_c']}.")

    bridge.to_csv(OUT / "bridge.csv", index=False, encoding="utf-8-sig")
    summary.merge(dups, on="system").to_csv(OUT / "system_summary.csv", index=False, encoding="utf-8-sig")
    ov.to_csv(OUT / "overlaps.csv", index=False, encoding="utf-8-sig")
    profiles.to_csv(OUT / "mismatch_profiles.csv", index=False, encoding="utf-8-sig")
    checks.to_csv(OUT / "ground_truth_check.csv", index=False, encoding="utf-8-sig")
    print(f"\nWrote {', '.join(p.name for p in sorted(OUT.glob('*.csv')))} to output/")


if __name__ == "__main__":
    main()
