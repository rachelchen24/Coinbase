"""
One entry point for the inherited-pipeline exercise.

    python run.py                # generate data -> baseline -> country cut -> tests -> measures -> trend check
    python run.py --no-generate

Prints a report (also saved to output/report.txt) and writes the deliverables to output/.
Exit code 1 if any test fails.
"""
from __future__ import annotations

import importlib
import inspect
import json
import runpy
import sys
import traceback

import pandas as pd

import trend_check
from lib import DATA, OUT, ROOT, connect, run, week, weeks


class Tee:
    def __init__(self, path):
        self.file, self.stdout = open(path, "w", encoding="utf-8"), sys.stdout

    def write(self, s):
        self.stdout.write(s)
        self.file.write(s)

    def flush(self):
        self.stdout.flush()
        self.file.flush()


def section(title: str) -> None:
    print(f"\n{'=' * 100}\n{title}\n{'=' * 100}")


def show(df: pd.DataFrame) -> None:
    print(df.to_string(index=False))


def run_tests(con) -> tuple[int, int]:
    passed = failed = 0
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        module = importlib.import_module(f"tests.{path.stem}")
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if not name.startswith("test_") or fn.__module__ != module.__name__:
                continue
            try:
                fn(con) if inspect.signature(fn).parameters else fn()
                passed += 1
                print(f"  [PASS] {path.stem}::{name}")
            except Exception as e:                                   # noqa: BLE001 - report every failure
                failed += 1
                print(f"  [FAIL] {path.stem}::{name}: {e}")
                traceback.print_exc(limit=1)
    return passed, failed


def main() -> int:
    OUT.mkdir(exist_ok=True)
    sys.stdout = Tee(OUT / "report.txt")
    if "--no-generate" not in sys.argv:
        section("Generate synthetic data")
        runpy.run_path(str(ROOT / "generate_data.py"), run_name="generate")
    gt = json.loads((DATA / "ground_truth.json").read_text())
    con = connect()
    all_weeks = weeks(con)
    first, last = all_weeks[0], all_weeks[-1]

    section("Step 2: freeze a baseline (v1 as-is, last 8 weeks, by country)")
    baseline = pd.concat([run(con, "weekly_active_traders", **week(w)).assign(week_start=w) for w in all_weeks[-8:]])
    baseline = baseline[["week_start", "country", "active_traders", "completed_trades"]].sort_values(
        ["week_start", "country"], na_position="last").reset_index(drop=True)
    path = OUT / "baseline_v1.csv"
    if path.exists():
        frozen = pd.read_csv(path, dtype={"week_start": str, "country": str}, keep_default_na=False, na_values=[""])
        same = frozen.equals(baseline.astype(frozen.dtypes.to_dict()))
        print(f"  baseline_v1.csv exists; current v1 output {'MATCHES' if same else 'DIFFERS FROM'} the frozen baseline")
    else:
        baseline.to_csv(path, index=False)
        print(f"  froze {len(baseline)} rows to {path.name}")
    print(baseline.groupby("week_start")[["active_traders", "completed_trades"]].sum().reset_index().to_string(index=False))

    section(f"Step 3: the Friday deliverable, country cut for the week of {last} (v1 definition, unchanged)")
    cut = run(con, "weekly_active_traders_by_country", **week(last))
    total = pd.DataFrame([{"country": "TOTAL", "active_traders": cut["active_traders"].sum(),
                           "completed_trades": cut["completed_trades"].sum()}])
    cut_out = pd.concat([cut, total], ignore_index=True)
    cut_out.to_csv(OUT / f"country_cut_{last}.csv", index=False)
    show(cut_out)
    rep = con.execute(f"select active_traders, completed_trades from reported_history "
                      f"where week_start = date '{last}'").fetchone()
    print(f"\n  dashboard reported for {last}: {rep[0]:,} active traders, {rep[1]:,} completed trades")

    section("Step 4: tests (known-answer fixture, reconciliation, v2 quality)")
    passed, failed = run_tests(con)
    print(f"\n  {passed} passed, {failed} failed")
    from tests.test_v2_quality import warn_week_over_week_change
    hits = warn_week_over_week_change(con)
    print(f"  [WARN] reported completed_trades moved >10% week over week: {hits}")

    section("Step 5: measure each issue separately (production untouched)")
    m = {w: {name: run(con, name, **week(w)).iloc[0] for name in
             ("measure_1_fanout", "measure_2_no_active_device", "measure_3_definition_mismatch",
              "measure_6_country_attribution", "measure_7_ledger_reconciliation")} for w in (first, last)}
    rows = [
        ("1 fan-out", "completed_trades inflation %", "measure_1_fanout", "inflation_pct"),
        ("1 fan-out", "avg active devices per trade", "measure_1_fanout", "avg_active_devices_per_trade"),
        ("2 no active device", "traders dropped", "measure_2_no_active_device", "traders_without_active_device"),
        ("2 no active device", "% of all traders dropped", "measure_2_no_active_device", "pct_of_traders_dropped"),
        ("2 no active device", "completed trades dropped", "measure_2_no_active_device", "completed_trades_dropped"),
        ("4 definitions", "active traders with 0 completed", "measure_3_definition_mismatch",
         "active_traders_with_zero_completed"),
        ("4 definitions", "% of v1 active traders", "measure_3_definition_mismatch", "pct_of_active_traders"),
        ("6 country", "trades placed in another country", "measure_6_country_attribution",
         "trades_placed_in_another_country"),
        ("6 country", "traders with NULL country", "measure_6_country_attribution", "traders_with_null_country"),
        ("ledger", "v1 completed_trades error vs ledger %", "measure_7_ledger_reconciliation",
         "completed_trades_error_pct"),
    ]
    def fmt(v, label: str) -> str:
        if "%" in label:
            return f"{v:.1f}"
        return f"{v:.2f}" if "avg" in label else f"{int(v):,}"

    measures = pd.DataFrame([(i, label, fmt(m[first][q][c], label), fmt(m[last][q][c], label))
                             for i, label, q, c in rows],
                            columns=["issue", "metric", f"week {first} (rerun today)", f"week {last}"])
    show(measures)
    measures.to_csv(OUT / "measures.csv", index=False)

    print("\nIssue 5: status codes (evidence for seeds/trade_status.csv; meanings UNVERIFIED)")
    status = run(con, "measure_4_status_codes", **week(last))
    show(status)
    s5 = status.set_index("status").loc[5]

    print(f"\nWhy the total looked plausible: the bugs offset each other (week of {last})")
    f1, f2, f7 = (m[last][k] for k in ("measure_1_fanout", "measure_2_no_active_device",
                                        "measure_7_ledger_reconciliation"))
    walk = pd.DataFrame([
        ("ledger: filled orders (truth)", int(f7["ledger_completed_trades"])),
        ("- completed trades of users with no active device (issue 2)", -int(f2["completed_trades_dropped"])),
        ("- status 5 fills v1 doesn't count (issue 5)", -int(s5["trades_this_week"])),
        ("= one row per completed trade, v1's population", int(f1["completed_trades_one_row_per_trade"])),
        ("+ extra rows from the device fan-out (issue 1)",
         int(f1["v1_completed_trades"] - f1["completed_trades_one_row_per_trade"])),
        ("= v1 completed_trades (what the dashboard shows)", int(f1["v1_completed_trades"])),
    ], columns=["step", "trades"])
    assert walk["trades"].iloc[[0, 1, 2]].sum() == walk["trades"].iloc[3], "walk does not add up"
    assert walk["trades"].iloc[[3, 4]].sum() == walk["trades"].iloc[5] == f7["v1_completed_trades"]
    show(walk)
    walk.to_csv(OUT / "ledger_to_v1_walk.csv", index=False)
    v2 = run(con, "weekly_active_traders_v2", **week(last))
    print(f"\n  v2 (PROPOSED, not shipped) for {last}: {v2['active_traders'].sum():,} active traders, "
          f"{v2['completed_trades'].sum():,} completed trades  vs. v1: {int(f7['v1_active_traders']):,} / "
          f"{int(f7['v1_completed_trades']):,}")

    print("\nIssue 3: history drift (dashboard value when reported vs. v1 rerun today)")
    drift = run(con, "measure_5_history_drift", first_week=first, last_week=last)
    drift.to_csv(OUT / "history_drift.csv", index=False)
    moved = drift[(drift["reported_active_traders"] != drift["rerun_active_traders"])
                  | (drift["reported_completed_trades"] != drift["rerun_completed_trades"])]
    show(drift.iloc[[0, 13, 26, 39, 52, 65, 76, 77]])
    print(f"\n  {len(moved)} of {len(drift)} weeks no longer reproduce. Active traders drift "
          f"{drift['active_traders_drift_pct'].min()}% to {drift['active_traders_drift_pct'].max()}%; "
          f"completed trades {drift['completed_trades_drift_pct'].min()}% to "
          f"{drift['completed_trades_drift_pct'].max()}%.")

    section("Step 6: trend check (trained on the stable period) + ledger reconciliation")
    tc = trend_check.run(con)
    tc["series"].to_csv(OUT / "trend_check.csv", index=False)
    for k, v in tc.items():
        if k != "series":
            print(f"  {k}: {v.date() if hasattr(v, 'date') else v}")
    print("  chart: output/trend_check.png")

    section("Scorecard: what the analysis found vs. what the generator injected")
    q = lambda sql: con.execute(sql).fetchone()[0]
    checks = [
        *[(f"{label} ({w})", gt[key][g], int(v)) for w, key in ((first, "first_week"), (last, "last_week"))
          for label, g, v in (
              ("fan-out extra rows", "fanout_extra_rows",
               m[w]["measure_1_fanout"]["v1_completed_trades"] - m[w]["measure_1_fanout"]["completed_trades_one_row_per_trade"]),
              ("traders without active device", "traders_without_active_device",
               m[w]["measure_2_no_active_device"]["traders_without_active_device"]),
              ("active traders with 0 completed", "v1_active_traders_with_zero_completed",
               m[w]["measure_3_definition_mismatch"]["active_traders_with_zero_completed"]),
              ("ledger completed trades", "ledger_completed_trades",
               m[w]["measure_7_ledger_reconciliation"]["ledger_completed_trades"]))],
        (f"status 5 trades ({last})", gt["last_week"]["status_5_trades"], int(s5["trades_this_week"])),
        ("status 5 first seen", gt["launch_fractional_status_5"], str(s5["first_seen"])[:10]),
        ("weeks that no longer reproduce", gt["weeks_where_rerun_differs_from_reported"], len(moved)),
        ("users without an active device", gt["users_without_active_device"],
         q("select count(*) from users where user_id not in (select user_id from devices where is_active = 1)")),
        ("users with NULL country", gt["users_null_country"], q("select count(*) from users where country is null")),
        ("users who changed country", gt["users_moved_country"],
         q("select count(*) from (select user_id from user_country_history group by 1 having count(*) > 1)")),
        ("divergence onset (trend check)", gt["launch_multi_device_sync"], str(tc["ratio_onset"].date())),
    ]
    score = pd.DataFrame(checks, columns=["check", "injected", "found"])
    score["result"] = (score["injected"].astype(str) == score["found"].astype(str)).map({True: "OK", False: "MISMATCH"})
    show(score)

    section("Summary")
    print(f"  tests: {passed} passed, {failed} failed | scorecard: {(score['result'] == 'OK').sum()}/{len(score)}")
    print(f"  Friday cut: output/country_cut_{last}.csv (sums to the reported {rep[0]:,} / {rep[1]:,})")
    print("  measures: output/measures.csv, output/history_drift.csv | trend: output/trend_check.png")
    sys.stdout.file.close()
    sys.stdout = sys.stdout.stdout
    return 1 if failed or (score["result"] != "OK").any() else 0


if __name__ == "__main__":
    sys.exit(main())
