-- severity: warn
-- protects: a stalled feed being read as users not converting
select
    source_system,
    last_ingested_at,
    sla_hours,
    round(date_diff('minute', last_ingested_at, {{ as_of }}) / 60.0, 1) as hours_since_last_load
from (
    select 'app_events' as source_system, max(ingested_at) as last_ingested_at, 2 as sla_hours from stg_app_events
    union all
    select 'kyc_vendor_events', max(ingested_at), 24 from stg_kyc_vendor_events
    union all
    select 'ledger_events', max(ingested_at), 6 from stg_ledger_events
) f
where date_diff('minute', last_ingested_at, {{ as_of }}) > sla_hours * 60
