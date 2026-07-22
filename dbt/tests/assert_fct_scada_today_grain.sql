-- Grain check: the merge key of fct_scada_today must be unique.
SELECT file, DUID, SETTLEMENTDATE, COUNT(*) AS n
FROM {{ ref('fct_scada_today') }}
GROUP BY ALL
HAVING COUNT(*) > 1
