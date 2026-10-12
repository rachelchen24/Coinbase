"""
Step 4: prove the Friday country cut changes nothing. Runs on the full dataset.
"""
from __future__ import annotations

import pandas as pd

from lib import run, week, weeks


def check_weeks(con) -> list[str]:
    """First week, the multi-device launch week, the fractional launch week, and the last 8 weeks."""
    all_weeks = weeks(con)
    return sorted({all_weeks[0], "2024-10-07", "2025-03-03", *all_weeks[-8:]})


def test_users_table_has_one_row_per_user(con):
    # otherwise users x trades fans out too, and country rows stop adding up to the total
    dupes = con.execute("select count(*) - count(distinct user_id) from users").fetchone()[0]
    assert dupes == 0, f"{dupes} duplicate user rows"


def test_country_cut_matches_v1_row_for_row(con):
    for w in check_weeks(con):
        v1 = run(con, "weekly_active_traders", **week(w)).assign(country=lambda d: d["country"].fillna("Unknown"))
        cut = run(con, "weekly_active_traders_by_country", **week(w))
        merged = v1.merge(cut, on="country", how="outer", suffixes=("_v1", "_cut"), indicator=True)
        assert (merged["_merge"] == "both").all(), f"{w}: country sets differ"
        for col in ("active_traders", "completed_trades"):
            assert (merged[f"{col}_v1"] == merged[f"{col}_cut"]).all(), f"{w}: {col} differs"


def test_countries_sum_to_total(con):
    for w in check_weeks(con):
        cut = run(con, "weekly_active_traders_by_country", **week(w))
        total = con.execute(f"""
            select count(distinct t.user_id), sum(case when t.status in (1, 3) then 1 else 0 end)
            from users u join trades t on t.user_id = u.user_id join devices d on d.user_id = t.user_id
            where t.created_at >= '{week(w)['week_start']}' and t.created_at < '{week(w)['week_end']}'
              and d.is_active = 1""").fetchone()
        assert (cut["active_traders"].sum(), cut["completed_trades"].sum()) == total, f"{w}: countries != total"


def test_latest_week_matches_number_already_reported(con):
    # the strongest check: Friday's country rows add up to exactly what the dashboard showed for that week
    w = weeks(con)[-1]
    cut = run(con, "weekly_active_traders_by_country", **week(w))
    rep = con.execute(f"select active_traders, completed_trades from reported_history "
                      f"where week_start = date '{w}'").fetchone()
    assert (cut["active_traders"].sum(), cut["completed_trades"].sum()) == rep


def test_null_country_is_a_visible_row(con):
    cut = run(con, "weekly_active_traders_by_country", **week(weeks(con)[-1]))
    assert "Unknown" in set(cut["country"]) and cut["country"].notna().all()
