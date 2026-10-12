-- severity: error
-- protects: every user enters the funnel at sign-up; cohorts and denominators depend on it
select user_key
from fct_onboarding_funnel
where signed_up_at is null
