-- Work queue of source identifiers we could not attribute to a user. Reviewed, not dropped.
select
    r.source_system,
    r.source_id,
    r.unresolved_reason,
    r.n_source_rows,
    coalesce(k.first_occurred_at, l.first_occurred_at)                    as first_occurred_at,
    coalesce(k.last_occurred_at, l.last_occurred_at)                      as last_occurred_at,
    k.emails_seen                                                         -- PII: for investigation only
from int_identity_resolution r
left join (
    select applicant_id, min(occurred_at) as first_occurred_at, max(occurred_at) as last_occurred_at,
           string_agg(distinct email_normalized, ', ') as emails_seen     -- Snowflake LISTAGG | BigQuery STRING_AGG
    from stg_kyc_vendor_events
    group by applicant_id
) k on r.source_system = 'kyc_vendor_events' and k.applicant_id = r.source_id
left join (
    select account_id, min(occurred_at) as first_occurred_at, max(occurred_at) as last_occurred_at
    from stg_ledger_events
    group by account_id
) l on r.source_system = 'ledger_events' and l.account_id = r.source_id
where r.user_key is null
