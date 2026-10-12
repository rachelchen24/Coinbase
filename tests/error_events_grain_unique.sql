-- severity: error
-- protects: the declared grain (user x step x attempt) and that no source event is modeled twice
select 'user_step_attempt' as grain, user_key || '|' || step_name || '|' || cast(attempt_number as varchar) as grain_key, count(*) as n_rows
from fct_onboarding_events
group by user_key, step_name, attempt_number
having count(*) > 1
union all
select 'source_event', source_system || '|' || source_event_id, count(*)
from fct_onboarding_events
group by source_system, source_event_id
having count(*) > 1
