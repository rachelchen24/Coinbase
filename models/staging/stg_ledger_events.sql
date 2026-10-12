-- One row per distinct ledger event. Redeliveries (same ledger_event_id) collapse to the first load.
with ranked as (
    select
        *,
        try_cast(event_epoch as bigint) as epoch_value,
        row_number() over (
            partition by ledger_event_id
            order by cast(_loaded_at as timestamptz)
        ) as delivery_rank
    from raw_ledger_events
)

select
    ledger_event_id,
    account_id,
    case event_type when 'deposit' then 'deposit' when 'trade' then 'trade' end as step_name,
    reference_id,                                       -- transfer_id for deposits, trade_id for trades
    lower(status)                                                         as status,
    try_cast(amount_usd as decimal(18, 2))                                as amount_usd,
    -- Two services emit epoch in different units. 1e11 seconds is year 5138 and 1e11 ms is 1973,
    -- so anything >= 1e11 must be milliseconds.
    case when epoch_value >= 100000000000 then 'ms' else 's' end         as epoch_unit,
    -- DuckDB to_timestamp(seconds) | Snowflake TO_TIMESTAMP_NTZ(v, 0|3) | BigQuery TIMESTAMP_SECONDS / TIMESTAMP_MILLIS
    to_timestamp(
        case when epoch_value >= 100000000000 then epoch_value / 1000.0 else epoch_value end
    ) at time zone 'UTC'                                                  as occurred_at,
    event_epoch                                                           as occurred_at_raw,
    cast(_loaded_at as timestamptz) at time zone 'UTC'                    as ingested_at
from ranked
where delivery_rank = 1
