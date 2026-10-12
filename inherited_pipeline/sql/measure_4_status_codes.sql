-- Issue 5: what do the undocumented status codes look like in the data? This is the evidence behind
-- seeds/trade_status.csv. Meanings stay UNVERIFIED until an engineer or the order-service code confirms them.
with ledger_trades as (select distinct trade_id from trade_ledger)

select
  t.status,
  count(*)                                                         as trades_all_time,
  count(case when t.created_at >= '{{ week_start }}' and t.created_at < '{{ week_end }}' then 1 end) as trades_this_week,
  cast(min(t.created_at) as date)                                  as first_seen,
  round(100.0 * avg(case when t.filled_quantity = t.quantity then 1 else 0 end), 1) as pct_fully_filled,
  round(100.0 * avg(case when t.filled_quantity = 0 then 1 else 0 end), 1)          as pct_zero_filled,
  round(100.0 * avg(case when t.quantity < 1 then 1 else 0 end), 1)                 as pct_fractional_qty,
  round(100.0 * avg(case when l.trade_id is not null then 1 else 0 end), 1)         as pct_in_ledger,
  t.status in (1, 3)                                               as counted_by_v1
from trades t
left join ledger_trades l on l.trade_id = t.trade_id
group by t.status
order by t.status
