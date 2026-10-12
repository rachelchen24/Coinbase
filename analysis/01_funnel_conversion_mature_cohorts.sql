-- Step-by-step conversion, strict flags, users who signed up >= 30 days before as-of (mature for every step). Last column shows the naive all-cohort number for contrast.
with f as (
    select * from fct_onboarding_funnel
),

steps as (
    select 1 as step_order, 'signed_up' as step,
           count(case when is_cohort_mature_first_trade then 1 end) as mature_users, count(*) as all_users
    from f
    union all
    select 2, 'email_verified',
           count(case when is_cohort_mature_first_trade and reached_email_verified then 1 end),
           count(case when reached_email_verified then 1 end)
    from f
    union all
    select 3, 'id_verified',
           count(case when is_cohort_mature_first_trade and reached_id_verified then 1 end),
           count(case when reached_id_verified then 1 end)
    from f
    union all
    select 4, 'first_deposit',
           count(case when is_cohort_mature_first_trade and reached_first_deposit then 1 end),
           count(case when reached_first_deposit then 1 end)
    from f
    union all
    select 5, 'first_trade',
           count(case when is_cohort_mature_first_trade and reached_first_trade then 1 end),
           count(case when reached_first_trade then 1 end)
    from f
)

select
    step,
    mature_users                                                               as users,
    round(100.0 * mature_users / first_value(mature_users) over (order by step_order), 1) as pct_of_signups,
    round(100.0 * mature_users / lag(mature_users) over (order by step_order), 1)         as pct_of_previous_step,
    round(100.0 * all_users / first_value(all_users) over (order by step_order), 1)       as naive_pct_all_cohorts
from steps
order by step_order
