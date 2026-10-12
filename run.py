"""
One entry point: generate data -> load raw -> build models -> run tests -> scorecard -> analysis.

    python run.py            # full rebuild; exits 1 if any error-severity test fails
    python run.py --no-generate

Builds warehouse.duckdb (query it afterwards with the duckdb CLI or Python) and writes analysis/results.md.
"""
from __future__ import annotations

import json
import re
import runpy
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).parent
SEEDS = ROOT / "seeds"
DB_PATH = ROOT / "warehouse.duckdb"

# dbt-style vars, substituted into every SQL file as {{ name }}
VARS = {
    "launch_at": "timestamp '2026-06-01 00:00:00'",
    "as_of": "timestamp '2026-10-01 00:00:00'",      # fixed "now" so results are reproducible
    "kyc_source_timezone": "America/New_York",
    "late_arriving_hours": 72,
    "mature_days_email_verified": 3,
    "mature_days_id_verified": 7,
    "mature_days_first_deposit": 14,
    "mature_days_first_trade": 30,
    "min_identity_match_rate": 0.98,
    "max_out_of_sequence_rate": 0.02,
    "anomaly_z": 3,
}

# build order (a hand-maintained DAG; dbt would infer it from ref())
MODELS = [
    "staging/stg_app_events",
    "staging/stg_kyc_vendor_events",
    "staging/stg_accounts",
    "staging/stg_ledger_events",
    "identity/int_identity_resolution",
    "identity/dim_user_identity",
    "identity/unresolved_identities",
    "marts/fct_onboarding_events",
    "marts/fct_onboarding_funnel",
    "marts/rpt_source_reconciliation",
]


def render(sql: str) -> str:
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(VARS[m.group(1)]), sql)


def fmt_value(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:,.1f}" if abs(v) >= 100 else f"{v:.3g}" if v != int(v) else f"{int(v)}"
    if isinstance(v, int) and not isinstance(v, bool):
        return f"{v:,}" if abs(v) >= 10000 else str(v)
    return str(v)


def table(cols: list[str], rows: list[tuple], limit: int | None = None) -> str:
    shown = rows[:limit] if limit else rows
    cells = [[fmt_value(v) for v in r] for r in shown]
    widths = [max(len(c), *(len(r[i]) for r in cells)) if cells else len(c) for i, c in enumerate(cols)]
    line = lambda vals: "| " + " | ".join(v.ljust(w) for v, w in zip(vals, widths)) + " |"
    out = [line(cols), "|" + "|".join("-" * (w + 2) for w in widths) + "|"] + [line(r) for r in cells]
    if limit and len(rows) > limit:
        out.append(f"... {len(rows) - limit} more rows")
    return "\n".join(out)


def query(con, sql: str):
    cur = con.execute(render(sql))
    return [d[0] for d in cur.description], cur.fetchall()


def header(title: str):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def main() -> int:
    if "--no-generate" not in sys.argv:
        header("1. Generate synthetic source data")
        runpy.run_path(str(ROOT / "generate_data.py"), run_name="generate")
    manifest = json.loads((SEEDS / "manifest.json").read_text())
    for name, n in manifest["source_rows"].items():
        print(f"  seeds/{name}.csv: {n:,} rows")

    DB_PATH.unlink(missing_ok=True)
    con = duckdb.connect(str(DB_PATH))
    con.execute("set TimeZone = 'UTC'")

    header("2. Load raw (every column as text) and build models")
    for name in ("app_events", "kyc_vendor_events", "accounts", "ledger_events"):
        con.execute(f"create table raw_{name} as select * from read_csv('{(SEEDS / name).as_posix()}.csv', "
                    "all_varchar = true, header = true)")
    con.execute(f"create table gt_pending_steps as select * from read_csv("
                f"'{(SEEDS / 'ground_truth' / 'pending_steps.csv').as_posix()}', header = true)")
    for model in MODELS:
        name = model.split("/")[-1]
        con.execute(f"create or replace table {name} as\n" + render((ROOT / "models" / f"{model}.sql").read_text()))
        n = con.execute(f"select count(*) from {name}").fetchone()[0]
        print(f"  built {name:<28} {n:>7,} rows")

    header("3. Data quality tests (each query returns failing rows)")
    results, failed_errors = {}, 0
    for path in sorted((ROOT / "tests").glob("*.sql")):
        sql = path.read_text()
        severity = re.search(r"--\s*severity:\s*(\w+)", sql).group(1)
        cols, rows = query(con, sql)
        results[path.stem] = (cols, rows)
        status = "PASS" if not rows else ("FAIL" if severity == "error" else "WARN")
        failed_errors += status == "FAIL"
        print(f"  [{status}] {severity:<5} {path.stem:<40} {len(rows)} failing row(s)")
        if rows:
            print("    " + table(cols, rows, limit=6).replace("\n", "\n    "))
    if failed_errors:
        print(f"\nBUILD BLOCKED: {failed_errors} error-severity test(s) failed. Marts must not be published.")
        return 1

    header("4. Scorecard: issues injected by the generator vs. what the warehouse detected")
    inj, exp = manifest["injected"], manifest["expected"]

    def scalar(sql: str):
        return con.execute(render(sql)).fetchone()[0]

    recon = {r[0]: r for r in con.execute("select * from rpt_source_reconciliation").fetchall()}
    unresolved = dict(con.execute("select source_system || ':' || unresolved_reason, count(*) "
                                  "from unresolved_identities group by 1").fetchall())
    late = dict(con.execute(render("""
        select source_system, count(*) from (
            select 'app_events' as source_system, occurred_at, ingested_at from stg_app_events
            union all select 'kyc_vendor_events', occurred_at, ingested_at from stg_kyc_vendor_events
            union all select 'ledger_events', occurred_at, ingested_at from stg_ledger_events
        ) s
        where occurred_at between {{ launch_at }} and {{ as_of }}
          and ingested_at > occurred_at + interval '{{ late_arriving_hours }} hours'
        group by 1""")).fetchall())
    anomalies = {(str(r[0]), r[1]) for r in results["warn_daily_volume_anomaly"][1]}
    stale = {r[0] for r in results["warn_source_freshness"][1]}
    pending_total, pending_immature = con.execute("""
        select count(*),
               count(case when not case g.pending_step
                    when 'email_verified' then f.is_cohort_mature_email_verified
                    when 'id_verified' then f.is_cohort_mature_id_verified
                    when 'first_deposit' then f.is_cohort_mature_first_deposit
                    when 'first_trade' then f.is_cohort_mature_first_trade end then 1 end)
        from gt_pending_steps g
        join fct_onboarding_funnel f on f.user_key = md5('app_events|' || cast(g.user_id as varchar))
    """).fetchone()

    checks = [
        *[(f"Duplicate rows removed: {s}", inj["duplicate_rows"][s], recon[s][2], "rpt_source_reconciliation")
          for s in ("app_events", "kyc_vendor_events", "ledger_events")],
        ("Unmatchable KYC applicants (no_match)", inj["unresolved_kyc_applicants"].get("no_match", 0),
         unresolved.get("kyc_vendor_events:no_match", 0), "unresolved_identities"),
        ("KYC applicants with ambiguous email", inj["unresolved_kyc_applicants"].get("ambiguous_email", 0),
         unresolved.get("kyc_vendor_events:ambiguous_email", 0), "unresolved_identities"),
        ("Ledger accounts with no signed-up user", inj["unresolved_ledger_accounts"],
         unresolved.get("ledger_events:account_user_not_signed_up", 0), "unresolved_identities"),
        ("Future-dated + pre-launch rows", inj["future_dated_rows"] + inj["pre_launch_rows"],
         len(results["warn_event_timestamps_in_range"][1]), "warn_event_timestamps_in_range"),
        ("Users out of sequence", exp["users_out_of_sequence"],
         scalar("select count(*) from fct_onboarding_funnel where not is_sequence_valid"), "is_sequence_valid"),
        ("Users with inferred email verification (gap)", exp["users_inferred_email_verified"],
         scalar("select count(*) from fct_onboarding_funnel where is_inferred_email_verified"),
         "is_inferred_email_verified"),
        ("Users with inferred ID verification (gap)", exp["users_inferred_id_verified"],
         scalar("select count(*) from fct_onboarding_funnel where is_inferred_id_verified"),
         "is_inferred_id_verified"),
        ("Returned deposits (transfers)", exp["returned_transfers"],
         scalar("select count(*) from fct_onboarding_events where outcome = 'returned'"), "fct_onboarding_events"),
        ("Users whose only deposits were returned", exp["users_only_returned_deposits"],
         scalar("select count(*) from fct_onboarding_funnel where has_returned_deposit and first_deposit_at is null"),
         "fct_onboarding_funnel"),
        *[(f"Late-arriving rows (>72h): {s}", inj["late_arriving_rows"][s], late.get(s, 0), "staging ingested_at")
          for s in ("app_events", "kyc_vendor_events", "ledger_events")],
        (f"Tracker outage {inj['tracker_outage']['day']} flagged", "yes",
         "yes" if (inj["tracker_outage"]["day"], "email_verification") in anomalies else "no",
         "warn_daily_volume_anomaly"),
        (f"Stale KYC feed ({inj['kyc_feed_stale_hours']}h) flagged", "yes",
         "yes" if "kyc_vendor_events" in stale else "no", "warn_source_freshness"),
    ]
    score = [(name, e, d, "OK" if e == d else "MISMATCH", where) for name, e, d, where in checks]
    # a fixed maturity window is a trade-off, not an identity: report coverage against a floor instead
    coverage = pending_immature / pending_total
    score.append(("Not-yet users kept out of drop-off denominators", f"{pending_total} (>=90%)",
                  f"{pending_immature} ({coverage:.0%})", "OK" if coverage >= 0.9 else "MISMATCH",
                  "is_cohort_mature_<step>"))
    print(table(["injected issue", "expected", "detected", "result", "caught by"], score))
    mismatches = sum(r[3] != "OK" for r in score)

    header("5. Analysis queries")
    md = ["# Analysis results\n", f"_Generated by `python run.py`; as-of {VARS['as_of']}._\n"]
    for path in sorted((ROOT / "analysis").glob("*.sql")):
        sql = path.read_text()
        title = re.search(r"--\s*(.+)", sql).group(1)
        cols, rows = query(con, sql)
        print(f"\n-- {path.name}: {title}\n" + table(cols, rows))
        md += [f"\n## {path.name}\n\n{title}\n\n" + table(cols, rows) + "\n"]
    (ROOT / "analysis" / "results.md").write_text("".join(md))

    header("Summary")
    warns = sum(1 for k, (_, r) in results.items() if k.startswith("warn") and r)
    print(f"  models: {len(MODELS)} built | tests: {len(results)} run, 0 errors, {warns} warnings"
          f" | scorecard: {len(score) - mismatches}/{len(score)} match")
    print(f"  warehouse: {DB_PATH.name} | analysis output: analysis/results.md")
    con.close()
    return 0 if mismatches == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
