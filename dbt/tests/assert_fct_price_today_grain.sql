-- Grain check: the merge key of fct_price_today must be unique.
SELECT file, REGIONID, SETTLEMENTDATE, INTERVENTION, COUNT(*) AS n
FROM {{ ref('fct_price_today') }}
GROUP BY ALL
HAVING COUNT(*) > 1
