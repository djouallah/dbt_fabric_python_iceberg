-- Grain check: the merge key of fct_scada_today must be unique.
-- Scoped to the 2026-07-23 merge-strategy cutover: rows written in the earlier
-- append era contain ~1k legacy duplicates that cannot be scrubbed until DuckDB
-- Iceberg gains a table rewrite (see duckdb-iceberg#1178). From the cutover, the
-- insert-only merge guarantees the grain and this test enforces it.
SELECT file, DUID, SETTLEMENTDATE, COUNT(*) AS n
FROM {{ ref('fct_scada_today') }}
WHERE DATE >= DATE '2026-07-23'
GROUP BY ALL
HAVING COUNT(*) > 1
