-- severity: error
-- protects: one row per user, so COUNT(*) on the funnel is a user count
select user_key, count(*) as n_rows
from fct_onboarding_funnel
group by user_key
having count(*) > 1
