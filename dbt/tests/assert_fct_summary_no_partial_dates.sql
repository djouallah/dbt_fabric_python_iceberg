-- Crater tripwire: a date that lands PARTIALLY in the summary is never revisited.
-- Mechanism: the intraday branch tops a date up hour by hour; if the pipeline stops
-- mid-day (outage), the date is left half-filled — and the daily backfill skips it
-- forever because its filter is `date NOT IN (SELECT date FROM summary)` and the
-- insert-only merge never updates matched keys (observed 2026-06-07 / 2026-06-14).
-- A completed date must cover (nearly) all 288 five-minute intervals; the summary's
-- first and last dates are excluded (source history may start mid-day; the latest
-- date is legitimately still filling).
-- Deliberately NOT tagged heavy: unlike the scada-vs-summary assertions this only
-- reads fct_summary itself, so CI's small process_limit can't false-positive it.
-- Remediation: DELETE the flagged dates from fct_summary — the next incremental
-- run's daily branch re-inserts them in full from fct_scada.

WITH per_date AS (
  SELECT
    date,
    COUNT(DISTINCT time) AS intervals
  FROM {{ ref('fct_summary') }}
  GROUP BY date
)

SELECT
  date,
  intervals
FROM per_date
WHERE date > (SELECT MIN(date) FROM per_date)
  AND date < (SELECT MAX(date) FROM per_date)
  AND intervals < 280
ORDER BY date
