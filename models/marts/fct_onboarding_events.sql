-- Grain: one row per user_key x step_name x attempt_number. The auditable source of truth:
-- every resolved, deduplicated attempt, including failures and rows with implausible timestamps (flagged).
with app as (
    select
        i.user_key, e.step_name, e.occurred_at, e.ingested_at,
        'app_events' as source_system, e.event_id as source_event_id,
        'success' as outcome, true as is_success
    from stg_app_events e
    join dim_user_identity i
      on i.source_system = 'app_events' and i.source_id = cast(e.user_id as varchar)
),

kyc as (
    select
        i.user_key, k.step_name, k.occurred_at, k.ingested_at,
        'kyc_vendor_events', k.check_id,
        k.check_status, k.is_success
    from stg_kyc_vendor_events k
    join dim_user_identity i
      on i.source_system = 'kyc_vendor_events' and i.source_id = k.applicant_id
),

-- A deposit attempt is a transfer: its status rows (initiated -> settled -> returned) collapse into one.
transfers as (
    select
        account_id,
        reference_id                                                      as transfer_id,
        min(case when status = 'initiated' then occurred_at end)          as initiated_at,
        min(case when status = 'settled' then occurred_at end)            as settled_at,
        min(case when status = 'returned' then occurred_at end)           as returned_at,
        max(ingested_at)                                                  as ingested_at
    from stg_ledger_events
    where step_name = 'deposit'
    group by account_id, reference_id
),

deposits as (
    select
        i.user_key, 'deposit',
        -- the step happens when the user initiates; success is decided by the final status
        coalesce(t.initiated_at, t.settled_at, t.returned_at), t.ingested_at,
        'ledger_events', t.transfer_id,
        case when t.returned_at is not null then 'returned'
             when t.settled_at is not null then 'settled'
             else 'pending' end,
        t.settled_at is not null and t.returned_at is null
    from transfers t
    join dim_user_identity i
      on i.source_system = 'ledger_events' and i.source_id = t.account_id
),

trades as (
    select
        i.user_key, l.step_name, l.occurred_at, l.ingested_at,
        'ledger_events', l.ledger_event_id,
        l.status, l.status = 'filled'
    from stg_ledger_events l
    join dim_user_identity i
      on i.source_system = 'ledger_events' and i.source_id = l.account_id
    where l.step_name = 'trade'
),

unioned as (
    select * from app
    union all select * from kyc
    union all select * from deposits
    union all select * from trades
)

select
    user_key,
    step_name,
    case step_name
        when 'sign_up' then 1
        when 'email_verification' then 2
        when 'identity_verification' then 3
        when 'deposit' then 4
        when 'trade' then 5
    end                                                                   as step_order,
    row_number() over (
        partition by user_key, step_name
        order by occurred_at, source_event_id
    )                                                                     as attempt_number,
    occurred_at,
    ingested_at,
    source_system,
    source_event_id,
    outcome,
    is_success,
    occurred_at between {{ launch_at }} and {{ as_of }}                   as is_timestamp_plausible,
    ingested_at > occurred_at + interval '{{ late_arriving_hours }} hours' as is_late_arriving
from unioned
