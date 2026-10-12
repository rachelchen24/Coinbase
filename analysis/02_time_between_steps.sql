-- Time between consecutive steps (strict converters, valid sequence, signed up >= 60 days before as-of to limit right-censoring), and whether the maturity windows cover ~p90 time-from-signup.
-- DuckDB median/quantile_cont | Snowflake MEDIAN / PERCENTILE_CONT(..) WITHIN GROUP | BigQuery APPROX_QUANTILES
-- DuckDB date_diff('second', a, b) | Snowflake DATEDIFF('second', a, b) | BigQuery TIMESTAMP_DIFF(b, a, SECOND)
with f as (
    select * from fct_onboarding_funnel
    where is_sequence_valid
      and signed_up_at <= {{ as_of }} - interval '60 days'
),

transitions as (
    select 1 as step_order, 'email_verified' as step, {{ mature_days_email_verified }} as maturity_window_days,
           date_diff('second', signed_up_at, email_verified_at) / 3600.0 as hours_from_previous_step,
           date_diff('second', signed_up_at, email_verified_at) / 86400.0 as days_from_signup
    from f where reached_email_verified
    union all
    select 2, 'id_verified', {{ mature_days_id_verified }},
           date_diff('second', email_verified_at, id_verified_at) / 3600.0,
           date_diff('second', signed_up_at, id_verified_at) / 86400.0
    from f where reached_id_verified
    union all
    select 3, 'first_deposit', {{ mature_days_first_deposit }},
           date_diff('second', id_verified_at, first_deposit_at) / 3600.0,
           date_diff('second', signed_up_at, first_deposit_at) / 86400.0
    from f where reached_first_deposit
    union all
    select 4, 'first_trade', {{ mature_days_first_trade }},
           date_diff('second', first_deposit_at, first_trade_at) / 3600.0,
           date_diff('second', signed_up_at, first_trade_at) / 86400.0
    from f where reached_first_trade
)

select
    step,
    count(*)                                                     as users,
    round(median(hours_from_previous_step), 1)                   as median_hours_from_previous_step,
    round(quantile_cont(hours_from_previous_step, 0.9), 1)       as p90_hours_from_previous_step,
    round(quantile_cont(days_from_signup, 0.9), 1)               as p90_days_from_signup,
    max(maturity_window_days)                                    as maturity_window_days
from transitions
group by step_order, step
order by step_order
