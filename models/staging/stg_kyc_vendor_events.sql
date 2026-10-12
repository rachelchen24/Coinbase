-- One row per vendor check (attempt). Webhook redeliveries (same check_id) collapse to the first load.
with ranked as (
    select
        *,
        row_number() over (
            partition by check_id
            order by cast(_loaded_at as timestamptz)
        ) as delivery_rank
    from raw_kyc_vendor_events
)

select
    check_id,
    applicant_id,
    try_cast(nullif(trim(external_ref), '') as bigint)                    as external_user_id,
    nullif(lower(trim(email)), '')                                        as email_normalized,
    'identity_verification'                                               as step_name,
    lower(trim(check_status))                                             as check_status,
    lower(trim(check_status)) = 'approved'                                as is_success,
    -- Naive local time. ASSUMPTION: the vendor account is configured for {{ kyc_source_timezone }}.
    -- DuckDB/Postgres: ts AT TIME ZONE tz | Snowflake: CONVERT_TIMEZONE(tz, 'UTC', ts)
    -- BigQuery: DATETIME(TIMESTAMP(ts, tz), 'UTC')
    (cast(created_at as timestamp) at time zone '{{ kyc_source_timezone }}') at time zone 'UTC'
                                                                          as occurred_at,
    created_at                                                            as occurred_at_raw,
    cast(_loaded_at as timestamptz) at time zone 'UTC'                    as ingested_at
from ranked
where delivery_rank = 1
