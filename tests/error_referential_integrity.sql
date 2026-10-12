-- severity: error
-- protects: every modeled event and funnel row belongs to a known user in the identity dimension
select 'fct_onboarding_events' as model, e.user_key
from fct_onboarding_events e
left join dim_user_identity i on i.user_key = e.user_key and i.source_system = 'app_events'
where i.user_key is null
union all
select 'fct_onboarding_funnel', f.user_key
from fct_onboarding_funnel f
left join dim_user_identity i on i.user_key = f.user_key and i.source_system = 'app_events'
where i.user_key is null
