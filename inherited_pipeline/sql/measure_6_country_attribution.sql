-- Issue 6: v1 attributes every trade to the user's CURRENT country. How many of this week's trades were
-- placed while the user lived elsewhere, and how many traders have no country at all?
with week_trades as (
  select t.trade_id, t.user_id, t.created_at, u.country as current_country
  from trades t
  join users u on u.user_id = t.user_id
  where t.created_at >= '{{ week_start }}' and t.created_at < '{{ week_end }}'
)

select
  count(*)                                                         as trades,
  count(case when h.country is distinct from w.current_country then 1 end) as trades_placed_in_another_country,
  count(distinct case when h.country is distinct from w.current_country then w.user_id end) as users_affected,
  count(distinct case when w.current_country is null then w.user_id end) as traders_with_null_country
from week_trades w
left join user_country_history h
  on h.user_id = w.user_id
 and w.created_at >= h.valid_from
 and (h.valid_to is null or w.created_at < h.valid_to)
