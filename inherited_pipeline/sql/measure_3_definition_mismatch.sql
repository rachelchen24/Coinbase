-- Issue 4: "active trader" counts any trade, "completed trades" only status 1 and 3. How many of v1's active
-- traders completed nothing that week (only cancelled, rejected or unknown-code orders)?
with v1_traders as (
  select
    t.user_id,
    count(distinct case when t.status in (1, 3) then t.trade_id end) as completed_trades
  from trades t
  join devices d on d.user_id = t.user_id and d.is_active = 1
  where t.created_at >= '{{ week_start }}' and t.created_at < '{{ week_end }}'
  group by t.user_id
)

select
  count(*)                                                         as v1_active_traders,
  count(case when completed_trades = 0 then 1 end)                 as active_traders_with_zero_completed,
  round(100.0 * count(case when completed_trades = 0 then 1 end) / count(*), 1) as pct_of_active_traders
from v1_traders
