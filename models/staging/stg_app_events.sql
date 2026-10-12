-- One row per distinct app event. Exact redeliveries (same event_id) collapse to the first load.
with ranked as (
    select
        *,
        row_number() over (
            partition by event_id
            order by cast(_loaded_at as timestamptz)
        ) as delivery_rank
    from raw_app_events
)

select
    event_id,
    try_cast(user_id as bigint)                                           as user_id,
    case event_name
        when 'sign_up' then 'sign_up'
        when 'email_verified' then 'email_verification'
    end                                                                   as step_name,
    nullif(lower(trim(email)), '')                                        as email_normalized,
    -- ISO-8601 carrying its own offset: parse as an instant, store as UTC wall-clock
    cast(event_ts as timestamptz) at time zone 'UTC'                      as occurred_at,
    event_ts                                                              as occurred_at_raw,
    cast(_loaded_at as timestamptz) at time zone 'UTC'                    as ingested_at
from ranked
where delivery_rank = 1
