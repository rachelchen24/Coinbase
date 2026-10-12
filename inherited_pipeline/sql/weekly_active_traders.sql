-- weekly_active_traders.sql  (v1, as inherited: core logic of the 400-line pipeline)
--
-- PRESERVED ON PURPOSE. The dashboard and every past review depend on this output, so it is not "fixed" in
-- place. The only change from the original is that the hard-coded dates are now parameters.
--
-- Known issues (each one measured by sql/measure_*.sql; see README for the numbers):
--   1. trades x devices fans out: completed_trades counts each trade once per active device
--   2. users with no active device are dropped from both columns (inner join + is_active = 1)
--   3. d.is_active is CURRENT state, so rerunning a past week gives a different answer
--   4. active_traders counts any trade (incl. cancelled/rejected); completed_trades only status 1, 3
--   5. status codes are undocumented; a new code (5, since 2025-03-03) is silently excluded
--   6. u.country is CURRENT country; NULL country is its own (easily missed) group
--   7. hygiene: created_at timezone unstated (assumed UTC); comma joins (one lost predicate = cross join)
--   Done right: the half-open date range (>= start, < end).
SELECT
  u.country,
  COUNT(DISTINCT t.user_id) AS active_traders,
  SUM(CASE WHEN t.status = 1 OR t.status = 3 THEN 1 ELSE 0 END) AS completed_trades
FROM users u, trades t, devices d
WHERE u.user_id = t.user_id
  AND t.user_id = d.user_id
  AND t.created_at >= '{{ week_start }}'
  AND t.created_at < '{{ week_end }}'
  AND d.is_active = 1
GROUP BY u.country
