-- Every raw row must end up deduplicated, modeled, or in unresolved_identities. No silent drops.
with raw_counts as (
    select 'app_events' as source_system, count(*) as raw_rows, count(distinct event_id) as distinct_rows
    from raw_app_events
    union all
    select 'kyc_vendor_events', count(*), count(distinct check_id) from raw_kyc_vendor_events
    union all
    select 'ledger_events', count(*), count(distinct ledger_event_id) from raw_ledger_events
),

-- each staged row, with the ids it is known by downstream
staged as (
    select 'app_events' as source_system, event_id as source_event_id, cast(user_id as varchar) as source_id
    from stg_app_events
    union all
    select 'kyc_vendor_events', check_id, applicant_id from stg_kyc_vendor_events
    union all
    select 'ledger_events', case when step_name = 'deposit' then reference_id else ledger_event_id end, account_id
    from stg_ledger_events
),

traced as (
    select
        s.source_system,
        count(*)                                                          as staged_rows,
        count(case when f.source_event_id is not null then 1 end)         as modeled_rows,
        count(case when f.source_event_id is null and u.source_id is not null then 1 end) as unresolved_rows,
        count(case when f.source_event_id is null and u.source_id is null then 1 end)     as unaccounted_rows
    from staged s
    left join (select distinct source_system, source_event_id from fct_onboarding_events) f
      on f.source_system = s.source_system and f.source_event_id = s.source_event_id
    left join unresolved_identities u
      on u.source_system = s.source_system and u.source_id = s.source_id
    group by s.source_system
)

select
    r.source_system,
    r.raw_rows,
    r.raw_rows - r.distinct_rows                                          as duplicate_rows_removed,
    t.staged_rows,
    t.modeled_rows,
    t.unresolved_rows,
    t.unaccounted_rows
from raw_counts r
join traced t on t.source_system = r.source_system
order by r.source_system
