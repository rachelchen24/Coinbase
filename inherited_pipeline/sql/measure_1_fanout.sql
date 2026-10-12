-- Issue 1: the devices join repeats each trade once per active device, inflating completed_trades.
-- Compared on the SAME population as v1 (users with an active device), so only the fan-out differs.
with active_devices as (
  select user_id, count(*) as n_devices from devices where is_active = 1 group by user_id
),

week_trades as (
  select t.trade_id, t.status, a.n_devices
  from trades t
  join active_devices a on a.user_id = t.user_id
  where t.created_at >= '{{ week_start }}' and t.created_at < '{{ week_end }}'
)

select
  sum(case when status in (1, 3) then n_devices else 0 end)        as v1_completed_trades,
  sum(case when status in (1, 3) then 1 else 0 end)                as completed_trades_one_row_per_trade,
  round(100.0 * sum(case when status in (1, 3) then n_devices else 0 end)
        / sum(case when status in (1, 3) then 1 else 0 end) - 100, 1) as inflation_pct,
  round(avg(n_devices), 2)                                         as avg_active_devices_per_trade
from week_trades
