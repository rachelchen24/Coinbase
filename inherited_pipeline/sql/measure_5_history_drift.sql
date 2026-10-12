-- Issue 3: d.is_active is current state. Rerun v1 today for every past week and compare with what the
-- dashboard originally reported for that week.
with weeks as (
  -- DuckDB generate_series | Snowflake: GENERATOR + DATEADD | BigQuery: GENERATE_DATE_ARRAY
  select cast(w as date) as week_start, cast(w + interval 7 day as date) as week_end
  from generate_series(date '{{ first_week }}', date '{{ last_week }}', interval 7 day) s(w)
),

rerun as (
  select
    wk.week_start,
    count(distinct t.user_id)                                      as active_traders,
    sum(case when t.status in (1, 3) then 1 else 0 end)            as completed_trades
  from weeks wk
  join trades t on t.created_at >= wk.week_start and t.created_at < wk.week_end
  join users u on u.user_id = t.user_id
  join devices d on d.user_id = t.user_id and d.is_active = 1
  group by wk.week_start
)

select
  r.week_start,
  r.active_traders                                                 as reported_active_traders,
  n.active_traders                                                 as rerun_active_traders,
  round(100.0 * n.active_traders / r.active_traders - 100, 1)      as active_traders_drift_pct,
  r.completed_trades                                               as reported_completed_trades,
  n.completed_trades                                               as rerun_completed_trades,
  round(100.0 * n.completed_trades / r.completed_trades - 100, 1)  as completed_trades_drift_pct
from reported_history r
join rerun n on n.week_start = r.week_start
order by r.week_start
