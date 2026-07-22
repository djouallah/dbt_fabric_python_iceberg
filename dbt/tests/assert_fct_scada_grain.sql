-- Grain check: the merge key of fct_scada must be unique.
-- Scoped to the 2026-07-23 merge-strategy cutover (see the _today grain tests for
-- the legacy-duplicate story).
{{ config(tags=['heavy']) }}

SELECT file, DUID, SETTLEMENTDATE, INTERVENTION, COUNT(*) AS n
FROM {{ ref('fct_scada') }}
WHERE DATE >= DATE '2026-07-23'
GROUP BY ALL
HAVING COUNT(*) > 1
