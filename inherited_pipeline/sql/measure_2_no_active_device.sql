-- Issue 2: traders with no active device (web-only, uninstalled) vanish from BOTH columns.
with traders as (
  select
    t.user_id,
    count(case when t.status in (1, 3) then 1 end)                 as completed_trades
  from trades t
  where t.created_at >= '{{ week_start }}' and t.created_at < '{{ week_end }}'
  group by t.user_id
)

select
  count(*)                                                         as all_traders,
  count(case when d.user_id is null then 1 end)                    as traders_without_active_device,
  round(100.0 * count(case when d.user_id is null then 1 end) / count(*), 1) as pct_of_traders_dropped,
  sum(case when d.user_id is null then completed_trades else 0 end) as completed_trades_dropped
from traders tr
left join (select distinct user_id from devices where is_active = 1) d on d.user_id = tr.user_id
