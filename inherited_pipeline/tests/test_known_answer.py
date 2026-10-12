"""
Known-answer fixture: a tiny dataset whose answers were worked out by hand. Any version that returns a
different answer fails.

Week of 2024-01-01:
  Maya (US)      2 active devices + 1 retired; trades: filled, partially filled, cancelled
                 (+ one filled trade at exactly 2024-01-08 00:00, which belongs to the NEXT week)
  Leo  (GB)      only a retired device (trades on web); trades: filled, filled
  Ana  (no country)  1 active device; trades: cancelled

By hand:
  v1 (as inherited)  US: 1 trader, 4 completed (Maya's 2 completed x 2 devices)
                     NULL: 1 trader (Ana, cancel-only), 0 completed.  Leo is missing.
  v2 (proposed)      US: 1 trader, 2 completed;  GB: 1 trader, 2 completed.  Ana completed nothing.
"""
from __future__ import annotations

import duckdb
import pandas as pd

from lib import SEEDS, render, run, week

FIXTURE = """
create table users (user_id int, country varchar, signup_at timestamp);
insert into users values (1, 'US', '2023-06-01'), (2, 'GB', '2023-06-01'), (3, null, '2023-06-01');
create table user_country_history (user_id int, country varchar, valid_from timestamp, valid_to timestamp);
insert into user_country_history values (1, 'US', '2023-06-01', null), (2, 'GB', '2023-06-01', null),
                                        (3, null, '2023-06-01', null);
create table devices (device_id int, user_id int, platform varchar, created_at timestamp, retired_at timestamp,
                      is_active int);
insert into devices values (1, 1, 'ios', '2023-06-01', null, 1), (2, 1, 'ipad', '2023-07-01', null, 1),
                           (3, 1, 'ios', '2023-01-01', '2023-06-01', 0), (4, 2, 'android', '2023-06-01', '2023-09-01', 0),
                           (5, 3, 'ios', '2023-06-01', null, 1);
create table trades (trade_id int, user_id int, created_at timestamp, status int, quantity double,
                     filled_quantity double);
insert into trades values (1, 1, '2024-01-02 14:00', 1, 10, 10), (2, 1, '2024-01-03 14:00', 3, 10, 4),
                          (3, 1, '2024-01-04 14:00', 2, 10, 0), (4, 1, '2024-01-08 00:00', 1, 10, 10),
                          (5, 2, '2024-01-02 15:00', 1, 5, 5), (6, 2, '2024-01-05 15:00', 1, 5, 5),
                          (7, 3, '2024-01-03 16:00', 2, 1, 0);
create table trade_ledger (ledger_id int, trade_id int, user_id int, filled_quantity double, executed_at timestamp);
insert into trade_ledger values (1, 1, 1, 10, '2024-01-02 14:00:01'), (2, 2, 1, 4, '2024-01-03 14:00:01'),
                                (3, 4, 1, 10, '2024-01-08 00:00:01'), (4, 5, 2, 5, '2024-01-02 15:00:01'),
                                (5, 6, 2, 5, '2024-01-05 15:00:01');
"""


def fixture() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(FIXTURE)
    con.execute(f"create table trade_status as select * from read_csv('{(SEEDS / 'trade_status.csv').as_posix()}', "
                "header = true)")
    return con


def as_dict(df) -> dict:
    return {(None if pd.isna(r[0]) else r[0]): (int(r[1]), int(r[2])) for r in df.itertuples(index=False)}


W = week("2024-01-01")


def test_v1_reproduces_hand_answer_including_its_bugs():
    # characterization test: pins CURRENT behaviour, so any "harmless" refactor that changes it is caught
    assert as_dict(run(fixture(), "weekly_active_traders", **W)) == {"US": (1, 4), None: (1, 0)}


def test_country_cut_matches_v1_on_fixture():
    assert as_dict(run(fixture(), "weekly_active_traders_by_country", **W)) == {"US": (1, 4), "Unknown": (1, 0)}


def test_v2_returns_correct_answer():
    assert as_dict(run(fixture(), "weekly_active_traders_v2", **W)) == {"US": (1, 2), "GB": (1, 2)}


def test_half_open_week_boundary():
    # Maya's trade at exactly 2024-01-08 00:00 belongs to the next week only
    nxt = run(fixture(), "weekly_active_traders_v2", **week("2024-01-08"))
    assert as_dict(nxt) == {"US": (1, 1)}
