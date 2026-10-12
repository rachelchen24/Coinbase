-- Engineering check on the one timestamp assumption we cannot verify from data alone: users whose ID verification would precede their email verification under each reading of the vendor's naive timestamps.
with first_id as (
    select
        e.user_key,
        min(k.occurred_at)                                       as id_verified_at_new_york,
        min(cast(k.occurred_at_raw as timestamp))                as id_verified_at_utc
    from fct_onboarding_events e
    join stg_kyc_vendor_events k on k.check_id = e.source_event_id
    where e.step_name = 'identity_verification' and e.is_success and e.is_timestamp_plausible
    group by e.user_key
)

select
    'America/New_York (assumed)'                                 as kyc_timestamp_reading,
    count(*)                                                     as users_with_both_steps,
    count(case when i.id_verified_at_new_york < f.email_verified_at then 1 end) as id_before_email
from first_id i
join fct_onboarding_funnel f on f.user_key = i.user_key
where f.email_verified_at is not null
union all
select
    'UTC',
    count(*),
    count(case when i.id_verified_at_utc < f.email_verified_at then 1 end)
from first_id i
join fct_onboarding_funnel f on f.user_key = i.user_key
where f.email_verified_at is not null
