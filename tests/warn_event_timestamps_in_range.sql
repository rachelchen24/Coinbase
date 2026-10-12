-- severity: warn
-- protects: clock skew / vendor defaults leaking into metrics; such rows are kept but excluded from the funnel
select source_system, step_name, source_event_id, occurred_at
from fct_onboarding_events
where not is_timestamp_plausible
