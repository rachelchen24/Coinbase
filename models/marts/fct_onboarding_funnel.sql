-- Grain: one row per user_key (every user with an app user_id). PM-facing; no PII.
-- Only rows with plausible timestamps count toward any step.
with steps as (
    select
        user_key,
        min(case when step_name = 'sign_up' then occurred_at end)                             as signed_up_at,
        min(case when step_name = 'email_verification' and is_success then occurred_at end)    as email_verified_at,
        min(case when step_name = 'identity_verification' and is_success then occurred_at end) as id_verified_at,
        -- first deposit = initiation time of the earliest transfer that settled and was NOT returned
        min(case when step_name = 'deposit' and is_success then occurred_at end)              as first_deposit_at,
        min(case when step_name = 'trade' and is_success then occurred_at end)                as first_trade_at,
        count(case when step_name = 'email_verification' then 1 end)                          as email_events,
        count(case when step_name = 'identity_verification' then 1 end)                       as id_verification_attempts,
        count(case when step_name = 'deposit' then 1 end)                                     as deposit_attempts,
        count(case when step_name = 'deposit' and outcome = 'returned' then 1 end)            as returned_deposits
    from fct_onboarding_events
    where is_timestamp_plausible
    group by user_key
),

users as (
    select i.user_key as anchor_user_key, s.*
    from dim_user_identity i
    left join steps s on s.user_key = i.user_key
    where i.source_system = 'app_events'
),

-- Latest earlier step, for the ordering check. A sentinel replaces NULL because GREATEST returns
-- NULL on any NULL argument in Snowflake/BigQuery (DuckDB ignores NULLs).
ordered as (
    select
        *,
        greatest(coalesce(signed_up_at, timestamp '1900-01-01'),
                 coalesce(email_verified_at, timestamp '1900-01-01'))                as max_before_id,
        greatest(coalesce(signed_up_at, timestamp '1900-01-01'),
                 coalesce(email_verified_at, timestamp '1900-01-01'),
                 coalesce(id_verified_at, timestamp '1900-01-01'))                   as max_before_deposit,
        greatest(coalesce(signed_up_at, timestamp '1900-01-01'),
                 coalesce(email_verified_at, timestamp '1900-01-01'),
                 coalesce(id_verified_at, timestamp '1900-01-01'),
                 coalesce(first_deposit_at, timestamp '1900-01-01'))                 as max_before_trade
    from users
),

flagged as (
    select
        *,
        case
            when email_verified_at < signed_up_at then 'email_verified'
            when id_verified_at < max_before_id then 'id_verified'
            when first_deposit_at < max_before_deposit then 'first_deposit'
            when first_trade_at < max_before_trade then 'first_trade'
        end                                                               as first_out_of_order_step
    from ordered
)

select
    anchor_user_key                                                       as user_key,

    -- cohort
    signed_up_at,
    cast(date_trunc('week', signed_up_at) as date)                        as signup_cohort_week,  -- ISO week (Monday)
    -- DuckDB date_diff | Snowflake DATEDIFF('day', a, b) | BigQuery DATE_DIFF(b, a, DAY)
    date_diff('day', cast(signed_up_at as date), cast({{ as_of }} as date)) as days_since_signup,

    -- first successful timestamp per step, as observed (never "repaired")
    email_verified_at,
    id_verified_at,
    first_deposit_at,
    first_trade_at,

    -- raw: this step succeeded, regardless of earlier steps
    email_verified_at is not null                                         as has_email_verified_event,
    id_verified_at is not null                                            as has_id_verified_event,
    first_deposit_at is not null                                          as has_first_deposit_event,
    first_trade_at is not null                                            as has_first_trade_event,

    -- strict: this step and every earlier step succeeded
    signed_up_at is not null and email_verified_at is not null            as reached_email_verified,
    signed_up_at is not null and email_verified_at is not null
        and id_verified_at is not null                                    as reached_id_verified,
    signed_up_at is not null and email_verified_at is not null
        and id_verified_at is not null and first_deposit_at is not null   as reached_first_deposit,
    signed_up_at is not null and email_verified_at is not null
        and id_verified_at is not null and first_deposit_at is not null
        and first_trade_at is not null                                    as reached_first_trade,

    -- inferred: no event of ANY outcome for this step, yet a later step succeeded. That is a tracking
    -- gap, as opposed to a failed attempt (declined check, returned deposit).
    coalesce(email_events, 0) = 0
        and coalesce(id_verified_at, first_deposit_at, first_trade_at) is not null
                                                                          as is_inferred_email_verified,
    coalesce(id_verification_attempts, 0) = 0
        and coalesce(first_deposit_at, first_trade_at) is not null        as is_inferred_id_verified,
    coalesce(deposit_attempts, 0) = 0 and first_trade_at is not null      as is_inferred_first_deposit,

    -- ordering: flagged, not fixed
    first_out_of_order_step is null                                       as is_sequence_valid,
    first_out_of_order_step,

    case
        when first_trade_at is not null then 'first_trade'
        when first_deposit_at is not null then 'first_deposit'
        when id_verified_at is not null then 'id_verified'
        when email_verified_at is not null then 'email_verified'
        else 'signed_up'
    end                                                                   as furthest_step_reached,

    coalesce(id_verification_attempts, 0)                                 as id_verification_attempts,
    coalesce(deposit_attempts, 0)                                         as deposit_attempts,
    coalesce(returned_deposits, 0) > 0                                    as has_returned_deposit,

    -- maturity: has the user had long enough that a missing step means "dropped off", not "not yet"?
    signed_up_at <= {{ as_of }} - interval '{{ mature_days_email_verified }} days' as is_cohort_mature_email_verified,
    signed_up_at <= {{ as_of }} - interval '{{ mature_days_id_verified }} days'    as is_cohort_mature_id_verified,
    signed_up_at <= {{ as_of }} - interval '{{ mature_days_first_deposit }} days'  as is_cohort_mature_first_deposit,
    signed_up_at <= {{ as_of }} - interval '{{ mature_days_first_trade }} days'    as is_cohort_mature_first_trade
from flagged
