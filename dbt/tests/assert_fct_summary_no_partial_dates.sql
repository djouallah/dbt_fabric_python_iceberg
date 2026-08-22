-- Crater tripwire: a date that lands PARTIALLY in the summary is never revisited.
-- Mechanism: the intraday branch tops a date up hour by hour; if the pipeline stops
-- mid-day (outage), the date is left half-filled — and the daily backfill skips it
-- forever because its filter is `date NOT IN (SELECT date FROM summary)` and the
-- insert-only merge never updates matched keys (observed 2026-06-07 / 2026-06-14).
-- A completed date must cover (nearly) all 288 five-minute intervals; the latest
-- date is excluded (legitimately still filling).
-- Scoped to a rolling 12-month window: craters only form going forward and get
-- remediated once flagged, so there is no point re-scanning frozen history every
-- run — the window also lets Iceberg prune the scan.
-- Deliberately NOT tagged heavy: unlike the scada-vs-summary assertions this only
-- reads fct_summary itself, so CI's small process_limit can't false-positive it.
-- Remediation: DELETE the flagged dates from fct_summary — the next incremental
-- run's daily backfill re-inserts them in full from fct_scada.
--
-- The `prev day loaded` guard excuses SOURCE gaps, which the remediation above
-- cannot fix. A date's first ~4h live in the PREVIOUS day's daily archive file
-- (a date spans 10:00 -> next-day 09:55 +10:00), so when that file was never
-- available the date is permanently short by ~49 intervals through no fault of
-- the pipeline — deleting it just rebuilds the same short day and the test fails
-- forever. Signature is a clean 239, and the pair sums to 288 when the following
-- date is also clipped. This used to be handled by asserting the known gaps
-- (2018-08-30/31, 2019-12-31/2020-01-01) sat outside the rolling window; that
-- broke when 2026-01-01 turned up inside it — its 2025-12-31 file is absent from
-- the archive entirely, so the day starts at 14:05 with 239 intervals.
-- Checking whether the previous day loaded states the actual mechanism instead of
-- hardcoding dates, and it stays a real tripwire: an outage leaves both
-- neighbours present, so genuine craters are still caught (verified against full
-- history — the guard excuses only 2018-04-01, 2018-06-27 and 2026-01-01, and
-- still flags the other eight, including 239-interval dates whose previous day
-- IS loaded).

WITH per_date AS (
  SELECT
    date,
    COUNT(DISTINCT time) AS intervals
  FROM {{ ref('fct_summary') }}
  WHERE date >= current_date - INTERVAL 12 MONTH
  GROUP BY date
)

SELECT
  date,
  intervals
FROM per_date p
WHERE date < (SELECT MAX(date) FROM per_date)
  AND intervals < 280
  -- Deliberately against the whole table, not per_date: the window's own first
  -- date has no predecessor inside the window and would be excused every run.
  AND EXISTS (
    SELECT 1 FROM {{ ref('fct_summary') }} s
    WHERE s.date = p.date - INTERVAL 1 DAY
  )
ORDER BY date
