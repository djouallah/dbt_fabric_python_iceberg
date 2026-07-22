-- Grain check: the merge key of fct_summary must be unique — the tripwire for any
-- residual concurrent-writer race.
-- Scoped to the 2026-07-23 merge-strategy cutover (see the _today grain tests for
-- the legacy-duplicate story).
{{ config(tags=['heavy']) }}

SELECT date, time, DUID, COUNT(*) AS n
FROM {{ ref('fct_summary') }}
WHERE date >= DATE '2026-07-23'
GROUP BY ALL
HAVING COUNT(*) > 1
