-- Same funnel, strict vs raw flags, by sign-up week (cohorts mature for ID verification). Watch the week of 2026-08-17, when the email tracker broke.
select
    signup_cohort_week,
    count(*)                                                                         as signups,
    round(100.0 * count(case when has_email_verified_event then 1 end) / count(*), 1) as email_verified_pct,
    count(case when is_inferred_email_verified then 1 end)                           as inferred_email_users,
    round(100.0 * count(case when has_id_verified_event then 1 end)
          / nullif(count(case when has_email_verified_event then 1 end), 0), 1)      as raw_id_per_email_pct,
    round(100.0 * count(case when has_id_verified_event then 1 end) / count(*), 1)    as raw_id_verified_pct,
    round(100.0 * count(case when reached_id_verified then 1 end) / count(*), 1)      as strict_id_verified_pct
from fct_onboarding_funnel
where is_cohort_mature_id_verified
group by signup_cohort_week
order by signup_cohort_week
