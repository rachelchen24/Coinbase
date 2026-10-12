-- severity: error
-- protects: no silent drops; every raw row is a duplicate, a modeled event, or an unresolved identity
select *
from rpt_source_reconciliation
where unaccounted_rows <> 0
   or raw_rows - duplicate_rows_removed <> staged_rows
