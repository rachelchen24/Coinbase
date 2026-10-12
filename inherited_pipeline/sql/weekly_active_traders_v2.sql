-- weekly_active_traders_v2.sql  PROPOSED. NOT SHIPPED until the PM agrees the definitions (README, Step 0).
--
-- Each choice below is a PM question; the v1 behaviour it replaces is in brackets.
--   active trader     = user with >= 1 completed trade in the week      [any trade, and only with an active device]
--   completed trade   = status flagged is_completed in trade_status     [hard-coded 1, 3; new codes dropped]
--   devices           = not used                                        [join fans out trades; drops web-only users]
--   country           = country at the END of the week (one per user-week, so countries still add up)
--                                                                       [current country]
-- Output is reproducible: nothing depends on current state, so rerunning a past week gives the same answer.
WITH week_trades AS (
  SELECT t.trade_id, t.user_id, s.is_completed
  FROM trades t
  LEFT JOIN trade_status s ON s.status_code = t.status       -- unknown codes -> NULL; tests fail on them
  WHERE t.created_at >= '{{ week_start }}'
    AND t.created_at <  '{{ week_end }}'
),

country_at_week_end AS (
  SELECT h.user_id, h.country
  FROM user_country_history h
  WHERE h.valid_from < '{{ week_end }}'
    AND (h.valid_to IS NULL OR h.valid_to >= '{{ week_end }}')
)

SELECT
  COALESCE(c.country, 'Unknown')                                  AS country,
  COUNT(DISTINCT CASE WHEN w.is_completed THEN w.user_id END)     AS active_traders,
  COUNT(CASE WHEN w.is_completed THEN 1 END)                      AS completed_trades
FROM week_trades w
LEFT JOIN country_at_week_end c ON c.user_id = w.user_id
GROUP BY COALESCE(c.country, 'Unknown')
HAVING COUNT(CASE WHEN w.is_completed THEN 1 END) > 0
ORDER BY active_traders DESC
