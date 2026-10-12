select
    account_id,
    try_cast(user_id as bigint)                                           as user_id,
    cast(opened_at as timestamptz) at time zone 'UTC'                     as opened_at,
    cast(_loaded_at as timestamptz) at time zone 'UTC'                    as ingested_at
from raw_accounts
