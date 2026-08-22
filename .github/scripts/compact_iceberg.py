"""Compact the OneLake Iceberg catalog's data files (post-load maintenance).

Every model in this project is an insert-only incremental merge — that is deliberate
(OneLake accepts one add-snapshot per commit), but it means each dbt run appends another
small data file per table and nothing ever folds them back together. This runs
iceberg_rewrite_data_files() over each table, consolidating files below the target size.

iceberg_rewrite_data_files landed in duckdb/duckdb-iceberg#1035 and is not in a stable
duckdb release yet — requirements.txt pins duckdb==1.6.0.dev365 (it self-identifies as
v2.0.0-alpha), and the iceberg extension binary is keyed to the duckdb build, so pinning
duckdb pins the extension too.

Ported from djouallah/analytics-as-code scripts/compact_iceberg.py, which runs this against
an R2-backed catalog. Two things differ here:
  - Credentials. That catalog vends storage credentials (CREATE SECRET TYPE ICEBERG).
    OneLake is attached with access_delegation_mode 'none', so the client brings its own
    Azure token — the same azure secret + attach options dbt/profiles.yml uses.
  - No S3 uploader tuning. The reference sets s3_uploader_max_parts_per_file for R2's
    "non-trailing parts must be equal length" rule; OneLake writes go through the azure
    extension over abfss:// and never touch the S3 uploader.

Exactly one iceberg_metadata() call, in prime(). Do not add more — it enumerates every
manifest, which on a fragmented table is the whole problem we're here to fix.

Known limitations of the upstream function:
  - manifest-level column statistics are not populated for rewritten files
  - V3 tables and partition spec evolution are unsupported
  - there is no snapshot expiry, so the pre-compaction data files stay in OneLake until
    something expires them. Reads get faster immediately; storage does not shrink.

Never fails the pipeline: every table is best-effort and errors are printed, not raised, so
a bad run just means the tables stay fragmented until the next one.

Usage:
    python .github/scripts/compact_iceberg.py
"""

import os
import sys
import time

import duckdb

ENDPOINT = os.environ["ONELAKE_ENDPOINT"]
TOKEN = os.environ["ONELAKE_TOKEN"]
WAREHOUSE = os.environ["WAREHOUSE_PATH"]      # "{workspace_id}/{lakehouse_id}"

# Files smaller than this get folded together; the rest are left alone.
TARGET_FILE_SIZE = "64MiB"
# Don't bother rewriting a table that only has a handful of files. Also what keeps
# already-tidy tables (dim_calendar, anything compacted last run) cheap.
MIN_INPUT_FILES = 5
# Stop starting new tables past this much wall clock, so the job reports what it did instead
# of being killed by the runner's timeout mid-rewrite. Keep it well under the workflow's
# timeout-minutes. Whatever gets skipped is picked up next run — compaction is incremental
# by nature.
BUDGET_MINUTES = float(os.environ.get("COMPACT_BUDGET_MINUTES", "70"))

# DuckDB's azure extension reads/writes OneLake over a curl transport (its default transport
# fails the OneLake TLS handshake). dbt sets this via on-run-start in dbt_project.yml; this
# script is not a dbt run, so it sets it itself. Same default, same env var.
AZURE_TRANSPORT = os.environ.get("AZURE_TRANSPORT_OPTION_TYPE", "default")

# Hand-ordered, not discovered — we know the models, and listing them costs a metadata scan
# per table for nothing. A new model just gets added here.
#
# Ordered by expected fragmentation and by what we'd least regret dropping if the budget runs
# out: the dashboard-facing tables first (they take an append every run and are what Power BI
# reads), then the staging log, then the small dimensions, then the big historical facts last
# — those are the slowest to rewrite and the least sensitive to small-file overhead.
TABLES = [
    "landing.fct_price_today",
    "landing.fct_scada_today",
    "mart.fct_summary",
    "mart.fct_summary_daily",
    "landing.stg_csv_archive_log",
    "mart.dim_calendar",
    "mart.dim_duid",
    "landing.fct_price",
    "landing.fct_scada",
]


def connect():
    con = duckdb.connect(":memory:")
    # Plain install first. duckdb 1.6.0.dev365 identifies itself as v2.0.0-alpha*, and
    # nightly-extensions.duckdb.org has no iceberg build under that version — asking
    # core_nightly first just buys a 404 and a scary log line. The core extension for this
    # build does carry iceberg_rewrite_data_files.
    try:
        con.install_extension("iceberg")
    except Exception as e:
        print(f"  (core install failed, trying core_nightly: {e})", flush=True)
        con.execute("FORCE INSTALL iceberg FROM core_nightly")
    con.load_extension("iceberg")

    con.execute(f"SET GLOBAL azure_transport_option_type = '{AZURE_TRANSPORT}'")
    con.execute("SET GLOBAL temp_directory = '/tmp/duckdb_spill'")

    # Mirrors dbt/profiles.yml. OneLake is attached with access_delegation_mode 'none' — the
    # catalog does not vend storage credentials, so the azure secret below is what authorises
    # the actual data-file reads and writes.
    con.execute(
        f"CREATE SECRET onelake_storage "
        f"(TYPE azure, PROVIDER access_token, ACCESS_TOKEN '{TOKEN}')"
    )
    con.execute(
        f"ATTACH '{WAREHOUSE}' AS onelake "
        f"(TYPE iceberg, ENDPOINT '{ENDPOINT}', TOKEN '{TOKEN}', "
        f"ACCESS_DELEGATION_MODE 'none')"
    )
    return con


def has_rewrite_function(con):
    return (
        con.execute(
            "SELECT count(*) FROM duckdb_functions() "
            "WHERE function_name = 'iceberg_rewrite_data_files'"
        ).fetchone()[0]
        > 0
    )


def prime(con, fq):
    """Make the table's storage credentials available to the rewrite.

    iceberg_rewrite_data_files doesn't fetch them itself — called cold it can die with 403
    "No credentials are provided" (duckdb/duckdb-iceberg#1349). The reference implementation
    tried the cheaper options (LIMIT 0, LIMIT 1) against a real catalog and both still 403'd:
    the 403 is on the manifest avro, and iceberg_metadata() is what reads those.

    It is expensive — it enumerates every manifest — so it is the one and only metadata call
    here. Don't add more, and don't "optimise" this one away.
    """
    con.execute(f"SELECT count(*) FROM iceberg_metadata('{fq}')")


def compact(con, table, say):
    """Compact one table. Returns (table, status) for the report."""
    fq = f"onelake.{table}"

    say("priming credentials")
    try:
        prime(con, fq)
    except Exception as e:
        return (table, f"ERROR priming: {type(e).__name__}: {e}")

    say("rewriting")
    try:
        row = con.execute(
            f"SELECT rewritten_data_files, added_data_files, rewritten_bytes "
            f"FROM iceberg_rewrite_data_files('{fq}', "
            f"target_file_size_bytes => '{TARGET_FILE_SIZE}', "
            f"min_input_files => {MIN_INPUT_FILES})"
        ).fetchone()
    except Exception as e:
        return (table, f"ERROR: {type(e).__name__}: {e}")

    # No row means nothing was eligible — not an error.
    rewritten, added, rewritten_bytes = row if row else (0, 0, 0)
    if not rewritten:
        return (table, f"skipped (<{MIN_INPUT_FILES} eligible files)")

    mb = (rewritten_bytes or 0) / 1048576.0
    return (table, f"OK ({rewritten} -> {added} files, {mb:.1f} MB rewritten)")


def report(lines, duckdb_version):
    out = ["=" * 100, f"Iceberg compaction (duckdb {duckdb_version})", "-" * 100]
    for table, status in lines:
        out.append(f"{table:<32}{status}")
    out.append("=" * 100)
    print("\n".join(out))

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"## 🧹 Iceberg compaction (duckdb {duckdb_version})\n\n")
            f.write("| table | result |\n|---|---|\n")
            for table, status in lines:
                f.write(f"| `{table}` | {status} |\n")
            f.write("\n")


def main():
    version = duckdb.__version__
    print(f"duckdb {version} — compacting {len(TABLES)} table(s) at {TARGET_FILE_SIZE}, "
          f"min_input_files={MIN_INPUT_FILES}, budget={BUDGET_MINUTES:g}min, in order:")
    for t in TABLES:
        print(f"  - onelake.{t}")
    print(flush=True)

    con = connect()
    if not has_rewrite_function(con):
        print(
            f"iceberg_rewrite_data_files() not available in duckdb {version} — the pinned "
            "build or its iceberg extension has moved. Nothing compacted."
        )
        return

    started = time.monotonic()
    total = len(TABLES)
    lines = []
    for i, table in enumerate(TABLES, 1):
        elapsed = (time.monotonic() - started) / 60.0
        if elapsed >= BUDGET_MINUTES:
            # Never drop tables silently — say which ones and why.
            for skipped in TABLES[i - 1:]:
                lines.append((skipped,
                              f"not attempted ({BUDGET_MINUTES:g}min budget spent)"))
            print(f"time budget spent after {elapsed:.1f}min — not attempting: "
                  f"{', '.join(TABLES[i - 1:])}", flush=True)
            break

        prefix = f"[{i}/{total}] onelake.{table}"

        def say(phase, _prefix=prefix):
            # Which step it's on, so a slow table can't be mistaken for a hang.
            print(f"{_prefix} ... {phase}", flush=True)

        print(f"{prefix} ... ({elapsed:.1f}min elapsed)", flush=True)
        _, status = compact(con, table, say)
        took = (time.monotonic() - started) / 60.0 - elapsed
        print(f"{prefix}: {status}  [{took:.1f}min]\n", flush=True)
        lines.append((table, status))

    report(lines, version)
    con.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Maintenance must never fail the pipeline.
        print(f"compact_iceberg failed (non-fatal): {e}", file=sys.stderr)
