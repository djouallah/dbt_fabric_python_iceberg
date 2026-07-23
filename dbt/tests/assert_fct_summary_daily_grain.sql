-- Grain check: the merge key of fct_summary_daily must be unique — tripwire for
-- any residual concurrent-writer race or a double-append of a date.
{{ config(tags=['heavy']) }}

SELECT date, DUID, COUNT(*) AS n
FROM {{ ref('fct_summary_daily') }}
GROUP BY ALL
HAVING COUNT(*) > 1
