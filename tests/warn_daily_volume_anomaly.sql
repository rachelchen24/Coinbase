-- severity: warn
-- protects: a broken tracker being reported as a real conversion drop
-- rule: a step's daily event count below mean - {{ anomaly_z }} sd of the trailing 14 days.
-- One-sided on purpose: spikes (double-firing) are already caught by dedup + grain tests, and a
-- two-sided rule on this data raised 7 false positives on ordinary Poisson noise.
with days as (
    -- DuckDB generate_series | Snowflake TABLE(GENERATOR(...)) | BigQuery GENERATE_DATE_ARRAY
    select cast(d as date) as day
    from generate_series(cast({{ launch_at }} as date), cast({{ as_of }} as date) - 1, interval 1 day) t(d)
),

steps as (
    select distinct step_name from fct_onboarding_events
),

daily as (
    select d.day, s.step_name, count(e.user_key) as n_events
    from days d
    cross join steps s
    left join fct_onboarding_events e
      on cast(e.occurred_at as date) = d.day and e.step_name = s.step_name and e.is_timestamp_plausible
    group by d.day, s.step_name
),

scored as (
    select
        *,
        avg(n_events) over w                                     as trailing_mean,
        stddev_samp(n_events) over w                             as trailing_sd,
        count(*) over w                                          as trailing_days
    from daily
    window w as (partition by step_name order by day rows between 14 preceding and 1 preceding)
)

select
    day,
    step_name,
    n_events,
    round(trailing_mean, 1)                                      as trailing_mean,
    round((n_events - trailing_mean) / trailing_sd, 1)           as z_score
from scored
where trailing_days = 14
  and trailing_sd > 0
  and n_events < trailing_mean - {{ anomaly_z }} * trailing_sd
order by day, step_name
