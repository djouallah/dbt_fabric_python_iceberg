-- Grain check: the merge key of fct_price must be unique.
-- Scoped to the 2026-07-23 merge-strategy cutover (see the _today grain tests for
-- the legacy-duplicate story).
{{ config(tags=['heavy']) }}

SELECT file, REGIONID, SETTLEMENTDATE, INTERVENTION, COUNT(*) AS n
FROM {{ ref('fct_price') }}
WHERE DATE >= DATE '2026-07-23'
GROUP BY ALL
HAVING COUNT(*) > 1
