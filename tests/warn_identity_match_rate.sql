-- severity: warn
-- protects: a vendor/integration change that silently stops attributing events to users
-- threshold: per-source match rate >= {{ min_identity_match_rate }}
select
    source_system,
    count(*)                                                     as source_ids,
    count(user_key)                                              as matched,
    round(count(user_key) * 1.0 / count(*), 4)                   as match_rate
from int_identity_resolution
where source_system <> 'app_events'
group by source_system
having count(user_key) * 1.0 / count(*) < {{ min_identity_match_rate }}
