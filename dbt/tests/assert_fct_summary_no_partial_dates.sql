-- Crater tripwire: a date that lands PARTIALLY in the summary is never revisited.
-- Mechanism: the intraday branch tops a date up hour by hour; if the pipeline stops
-- mid-day (outage), the date is left half-filled — and the daily backfill skips it
-- forever because its filter is `date NOT IN (SELECT date FROM summary)` and the
-- insert-only merge never updates matched keys (observed 2026-06-07 / 2026-06-14).
-- A completed date must cover (nearly) all 288 five-minute intervals; the latest
-- date is excluded (legitimately still filling).
-- Covers all of history. Two by-design SOURCE gaps are carved out explicitly:
-- a missing daily archive file clips ~4h off one date and ~20h off the next
-- (2018-08-30/31 and 2019-12-31/2020-01-01 — pairs that sum to 288). These have
-- no upstream file to backfill from, so they are permanently partial by design
-- and must never fail this. Excluding them by name (rather than a rolling window)
-- keeps the tripwire watching the full table for real craters.
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
WHERE date < (SELECT MAX(date) FROM per_date)
  AND intervals < 280
  AND date NOT IN (
    DATE '2018-08-30', DATE '2018-08-31',   -- missing daily archive file
    DATE '2019-12-31', DATE '2020-01-01'    -- missing daily archive file
  )
ORDER BY date
