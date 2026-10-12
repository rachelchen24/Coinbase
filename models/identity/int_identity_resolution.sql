-- Every source identifier, resolved (or not) to a user_key. Deterministic rules only, tried in
-- confidence order; the first rule that yields exactly one user wins:
--   1 exact_id          app user_id (the anchor itself)
--   2 vendor_reference  KYC external_ref = our user_id    | account_mapping: ledger account -> accounts.user_id
--   3 normalized_email  lower(trim(email)) matches exactly one sign-up email
-- Anything else is kept with an unresolved_reason, never dropped.
with anchors as (
    select
        user_id,
        md5('app_events|' || cast(user_id as varchar))                    as user_key,
        max(case when step_name = 'sign_up' then email_normalized end)    as email_normalized,
        count(*)                                                          as n_source_rows
    from stg_app_events
    where user_id is not null
    group by user_id
),

email_lookup as (
    select email_normalized, min(user_key) as user_key, count(*) as n_users
    from anchors
    where email_normalized is not null
    group by email_normalized
),

kyc_applicants as (
    select
        applicant_id,
        count(distinct external_user_id)                                  as n_refs,
        min(external_user_id)                                             as external_user_id,
        count(*)                                                          as n_source_rows
    from stg_kyc_vendor_events
    group by applicant_id
),

kyc_email_matches as (
    -- every distinct email an applicant used, matched to sign-up emails
    select
        k.applicant_id,
        count(distinct e.user_key)                                        as n_email_users,
        max(e.n_users)                                                    as max_users_per_email,
        min(e.user_key)                                                   as email_user_key
    from (select distinct applicant_id, email_normalized from stg_kyc_vendor_events) k
    left join email_lookup e on e.email_normalized = k.email_normalized
    group by k.applicant_id
),

kyc as (
    select
        'kyc_vendor_events'                                               as source_system,
        k.applicant_id                                                    as source_id,
        k.n_source_rows,
        case
            when k.n_refs = 1 and a.user_key is not null then a.user_key
            when k.n_refs = 0 and m.n_email_users = 1 and m.max_users_per_email = 1 then m.email_user_key
        end                                                               as user_key,
        case
            when k.n_refs = 1 and a.user_key is not null then 'vendor_reference'
            when k.n_refs = 0 and m.n_email_users = 1 and m.max_users_per_email = 1 then 'normalized_email'
        end                                                               as match_method,
        case
            when k.n_refs > 1 then 'conflicting_vendor_reference'
            when k.n_refs = 1 and a.user_key is null then 'vendor_reference_not_found'
            when m.n_email_users > 1 or m.max_users_per_email > 1 then 'ambiguous_email'
            else 'no_match'
        end                                                               as unresolved_reason
    from kyc_applicants k
    left join anchors a on a.user_id = k.external_user_id
    left join kyc_email_matches m on m.applicant_id = k.applicant_id
),

ledger as (
    select
        'ledger_events'                                                   as source_system,
        l.account_id                                                      as source_id,
        count(*)                                                          as n_source_rows,
        min(a.user_key)                                                   as user_key,
        case when min(a.user_key) is not null then 'account_mapping' end  as match_method,
        case when min(acc.account_id) is null then 'account_not_in_accounts_table'
             else 'account_user_not_signed_up' end                        as unresolved_reason
    from stg_ledger_events l
    left join stg_accounts acc on acc.account_id = l.account_id
    left join anchors a on a.user_id = acc.user_id
    group by l.account_id
),

app as (
    select
        'app_events'                                                      as source_system,
        cast(a.user_id as varchar)                                        as source_id,
        a.n_source_rows,
        a.user_key,
        'exact_id'                                                        as match_method,
        cast(null as varchar)                                             as unresolved_reason
    from anchors a
)

select
    source_system,
    source_id,
    n_source_rows,
    user_key,
    match_method,
    case match_method
        when 'exact_id' then 1
        when 'vendor_reference' then 2
        when 'account_mapping' then 2
        when 'normalized_email' then 3
    end                                                                   as match_rank,
    case when user_key is null then unresolved_reason end                 as unresolved_reason
from (
    select * from app
    union all select * from kyc
    union all select * from ledger
) resolved
