-- (source_system, source_id) -> user_key for every resolved identifier.
-- The only model that holds PII (sign-up email, on the anchor row) so the marts never need to.
select
    r.user_key,
    r.source_system,
    r.source_id,
    r.match_method,
    r.match_rank,
    case when r.source_system = 'app_events' then a.email_normalized end as email_normalized
from int_identity_resolution r
left join (
    select cast(user_id as varchar) as source_id, max(email_normalized) as email_normalized
    from stg_app_events
    where step_name = 'sign_up'
    group by user_id
) a on r.source_system = 'app_events' and a.source_id = r.source_id
where r.user_key is not null
