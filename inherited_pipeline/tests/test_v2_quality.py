"""
Tests that ship WITH v2 (Step 8). They run now so the proposal is proven before anyone is asked to adopt it.
"""
from __future__ import annotations

from lib import DATA, run, week, weeks


def test_no_unknown_status_codes(con):
    unknown = con.execute("""
        select distinct t.status from trades t
        left join trade_status s on s.status_code = t.status
        where s.status_code is null""").df()
    assert unknown.empty, f"status codes missing from seeds/trade_status.csv: {unknown['status'].tolist()}"


def test_v2_has_no_fan_out_and_reconciles_to_ledger(con):
    # one row per trade: v2's completed trades equal the ledger's distinct filled orders, every week checked
    for w in weeks(con)[::13] + weeks(con)[-1:]:
        v2 = run(con, "weekly_active_traders_v2", **week(w))
        ledger = con.execute(f"""
            select count(distinct l.trade_id), count(distinct l.user_id)
            from trade_ledger l join trades t on t.trade_id = l.trade_id
            where t.created_at >= '{week(w)['week_start']}' and t.created_at < '{week(w)['week_end']}'""").fetchone()
        assert (v2["completed_trades"].sum(), v2["active_traders"].sum()) == ledger, f"{w}: v2 != ledger"


def test_v2_ignores_current_device_state(con):
    # v1's history drifts because it reads devices.is_active today. Wipe the devices table (inside a
    # rolled-back transaction): v2's answer for a past week must not move.
    w = week(weeks(con)[0])
    before = run(con, "weekly_active_traders_v2", **w)
    con.execute("begin")
    con.execute(f"create or replace view devices as select * from read_parquet('{(DATA / 'devices.parquet').as_posix()}') "
                "where false")
    after = run(con, "weekly_active_traders_v2", **w)
    con.execute("rollback")
    assert before.equals(after)


def warn_week_over_week_change(con, threshold: float = 0.10) -> list[tuple]:
    """Alert (not a hard failure) when the reported metric moves more than `threshold` week over week."""
    rep = con.execute("select week_start, completed_trades from reported_history order by 1").df()
    rep["change"] = rep["completed_trades"].pct_change()
    hits = rep[rep["change"].abs() > threshold]
    return [(str(r.week_start.date()), round(100 * r.change, 1)) for r in hits.itertuples()]
