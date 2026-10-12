-- weekly_active_traders_by_country.sql  (the Friday deliverable)
--
-- The v1 definition, UNCHANGED: same joins, same filters, same quirks (including the fan-out and the dropped
-- no-device users). tests/test_reconciliation.py proves it matches weekly_active_traders.sql row for row and
-- that the countries add up to the reported total.
--
-- Changes that cannot alter results:
--   explicit JOINs instead of comma joins (same inner-join semantics, but a lost ON clause is now a syntax error)
--   status IN (1, 3) instead of status = 1 OR status = 3
--   NULL country shown as 'Unknown' so it isn't missed on a slide (display only; no real country uses that label)
SELECT
  COALESCE(u.country, 'Unknown')                                  AS country,
  COUNT(DISTINCT t.user_id)                                       AS active_traders,
  SUM(CASE WHEN t.status IN (1, 3) THEN 1 ELSE 0 END)             AS completed_trades
FROM users u
JOIN trades t  ON t.user_id = u.user_id
JOIN devices d ON d.user_id = t.user_id
WHERE t.created_at >= '{{ week_start }}'
  AND t.created_at <  '{{ week_end }}'
  AND d.is_active = 1
GROUP BY COALESCE(u.country, 'Unknown')
ORDER BY active_traders DESC
