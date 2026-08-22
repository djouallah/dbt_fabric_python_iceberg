-- Crater tripwire: a date that lands PARTIALLY in the summary and is then never completed.
-- Mechanism: during backfill a date enters the summary with only the intervals fct_scada
-- happened to hold at that moment (typically the 49 that came from the previous day's archive
-- file); the daily branch used to skip any date already present, so the rest of the day never
-- arrived. fct_summary now reprocesses dates it holds partially, which heals these — this test
-- is the guard that it keeps working.
--
-- Compares the summary against fct_scada rather than against a fixed 288, because "short" and
-- "wrong" are not the same thing. Some dates are short at the SOURCE and always will be: a
-- date's first ~4h live in the PREVIOUS day's daily archive file, so when that file was never
-- published the date is permanently capped (2026-01-01 sits at 239, 2025-11-12 at 49, and
-- fct_scada agrees with both). Those are not craters and no amount of reprocessing fixes them.
-- A real crater is the summary holding FEWER intervals than fct_scada actually has — the
-- pipeline losing data it was handed. Observed 2025-09-13: 49 in the summary, 288 in scada.
--
-- The 280 pre-filter is what keeps this cheap enough to run in CI unTAGGED. It is not the
-- assertion, just a cheap shortlist off fct_summary alone; only those few dates are then
-- probed against fct_scada, so Iceberg prunes to a handful of dates instead of aggregating a
-- year of scada (measured: ~46s probing 2 dates vs ~233s for the full-year aggregate).
-- The latest date is excluded (legitimately still filling).
-- Scoped to a rolling 12-month window: craters only form going forward, and the window lets
-- Iceberg prune the scan.
-- Remediation should be automatic now — the next run whose daily branch fires refills the
-- date. If a date stays flagged across several daily runs, that filter has regressed.

WITH per_date AS (
  SELECT
    date,
    COUNT(DISTINCT time) AS intervals
  FROM {{ ref('fct_summary') }}
  WHERE date >= current_date - INTERVAL 12 MONTH
  GROUP BY date
),

-- Cheap shortlist: only these get compared against the source.
short AS (
  SELECT date, intervals
  FROM per_date
  WHERE date < (SELECT MAX(date) FROM per_date)
    AND intervals < 280
)

SELECT *
FROM (
  SELECT
    s.date,
    s.intervals AS summary_intervals,
    (
      SELECT COUNT(DISTINCT sc.SETTLEMENTDATE)
      FROM {{ ref('fct_scada') }} sc
      WHERE sc.DATE = s.date
        AND sc.INTERVENTION = 0
        AND sc.INITIALMW <> 0
    ) AS scada_intervals
  FROM short s
)
WHERE summary_intervals < scada_intervals
ORDER BY date
