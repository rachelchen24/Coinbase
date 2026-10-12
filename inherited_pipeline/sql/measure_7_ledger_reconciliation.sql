-- Reconciliation against an independent source: the clearing ledger (one row per execution).
-- Catches errors that are constant over time, which a trend model trained on biased history cannot see.
with v1 as (
  select
    count(distinct t.user_id)                                      as active_traders,
    sum(case when t.status in (1, 3) then 1 else 0 end)            as completed_trades
  from users u
  join trades t on t.user_id = u.user_id
  join devices d on d.user_id = t.user_id
  where t.created_at >= '{{ week_start }}' and t.created_at < '{{ week_end }}' and d.is_active = 1
),

ledger as (
  select
    count(distinct l.trade_id)                                     as completed_trades,
    count(distinct l.user_id)                                      as traders_with_a_fill
  from trade_ledger l
  join trades t on t.trade_id = l.trade_id                         -- order time defines the week, as in v1
  where t.created_at >= '{{ week_start }}' and t.created_at < '{{ week_end }}'
)

select
  v1.completed_trades                                              as v1_completed_trades,
  ledger.completed_trades                                          as ledger_completed_trades,
  round(100.0 * v1.completed_trades / ledger.completed_trades - 100, 1) as completed_trades_error_pct,
  v1.active_traders                                                as v1_active_traders,
  ledger.traders_with_a_fill                                       as ledger_traders_with_a_fill
from v1
cross join ledger
