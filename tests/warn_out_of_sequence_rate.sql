-- severity: warn
-- protects: a timezone or parsing regression, which shows up as a jump in out-of-order users
-- threshold: share of users with is_sequence_valid = false <= {{ max_out_of_sequence_rate }}
select
    count(*)                                                     as users,
    count(case when not is_sequence_valid then 1 end)            as out_of_sequence_users,
    round(count(case when not is_sequence_valid then 1 end) * 1.0 / count(*), 4) as out_of_sequence_rate
from fct_onboarding_funnel
having count(case when not is_sequence_valid then 1 end) * 1.0 / count(*) > {{ max_out_of_sequence_rate }}
