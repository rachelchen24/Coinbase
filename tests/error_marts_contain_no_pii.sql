-- severity: error
-- protects: emails stay in the identity layer; marts are safe to share widely
select table_name, column_name
from information_schema.columns
where table_name in ('fct_onboarding_funnel', 'fct_onboarding_events')
  and (lower(column_name) like '%email%' and lower(column_name) not like '%email_verified%'
       or lower(column_name) like '%phone%' or lower(column_name) like '%name%' and column_name <> 'step_name')
