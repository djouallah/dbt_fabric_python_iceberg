-- Pre-aggregated daily rollup of fct_summary at the (date, DUID) grain. The
-- dashboard's "day" grain (any span >31 days — a whole year, say) reads THIS
-- table instead of rolling up the raw 5-minute fct_summary in duckdb-wasm: a
-- year of 5-min data is tens of millions of rows, this is ~365 × (#DUIDs).
--
-- Insert-only merge, exactly like fct_summary and for the same reason: the
-- OneLake Iceberg REST catalog allows only ONE add-snapshot update per commit,
-- so a merge with a WHEN MATCHED UPDATE branch (delete files + data files in one
-- commit) is rejected BadRequest 400. WHEN MATCHED DO NOTHING keeps every run a
-- single append snapshot — we backfill only whole days missing from this table.
--
-- The current (max) date of fct_summary is EXCLUDED: it is still filling
-- intraday, and an insert-only merge could never correct a frozen partial day.
-- Once the day rolls over it becomes complete and is appended on the next run.
-- Recent data is served by the dashboard's finer (hour / 5-min) grains anyway.
-- `dbt run --full-refresh` is the reconciliation lever.
{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key=['date', 'DUID'],
    merge_clauses={'when_matched': [{'action': 'do_nothing'}]},
    schema='mart'
) }}

SELECT
    date,
    DUID,
    CAST(AVG(mw) AS DECIMAL(18, 4)) AS mw,           -- mean unit output across the day (MW)
    CAST(SUM(mw) / 12 AS DECIMAL(18, 4)) AS mwh,     -- daily energy (each 5-min interval = mw / 12 MWh)
    CAST(AVG(price) AS DECIMAL(18, 4)) AS price,     -- mean regional price seen by the unit ($/MWh)
    MAX(cutoff) AS cutoff
FROM {{ ref('fct_summary') }}
WHERE date < (SELECT MAX(date) FROM {{ ref('fct_summary') }})   -- skip the still-filling current day
{% if is_incremental() %}
  AND date NOT IN (SELECT DISTINCT date FROM {{ this }})
{% endif %}
GROUP BY date, DUID
