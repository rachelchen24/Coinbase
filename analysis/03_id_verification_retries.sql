-- ID verification retry rate and its effect on deposit conversion (users who attempted KYC and are mature for first deposit).
select
    case when id_verification_attempts >= 3 then '3+' else cast(id_verification_attempts as varchar) end as id_attempts,
    count(*)                                                                         as users,
    round(100.0 * count(*) / sum(count(*)) over (), 1)                               as pct_of_kyc_users,
    round(100.0 * count(case when has_id_verified_event then 1 end) / count(*), 1)    as pct_eventually_verified,
    round(100.0 * count(case when reached_first_deposit then 1 end)
          / nullif(count(case when reached_id_verified then 1 end), 0), 1)           as pct_deposit_given_verified
from fct_onboarding_funnel
where is_cohort_mature_first_deposit
  and id_verification_attempts > 0
group by 1
order by 1
