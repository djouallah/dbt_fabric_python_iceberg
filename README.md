# ⚠️ Experimental 🦆

> [!CAUTION]
> **This is not for production systems. Experimental and educational purposes only.**
>
> _Also requires OneLake Iceberg write (private preview, limited access)._
>
> OneLake Iceberg write is mainly for third-party interoperability — think Snowflake, etc.
>
> That said, DuckDB's Iceberg support is maturing fast: **1.4.5** was the first release that
> basically works, **1.5.3** added `MERGE INTO` (and `ALTER TABLE`), and **2.0** is set to add
> table maintenance, retries, and more. Run the latest.

---

# dbt + DuckDB + OneLake Iceberg REST Catalog

[![Pipeline](https://github.com/djouallah/dbt_fabric_python_iceberg/actions/workflows/pipeline.yml/badge.svg)](https://github.com/djouallah/dbt_fabric_python_iceberg/actions/workflows/pipeline.yml)
[![dbt docs](https://img.shields.io/badge/dbt%20docs-live-blue)](https://djouallah.github.io/dbt_fabric_python_iceberg/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Iceberg is cool. The whole pipeline runs anywhere Python runs — your laptop, a GitHub Actions runner, a container, an AI agent. The Delta Lake version of this pipeline lives at <https://github.com/djouallah/dbt_fabric_python_delta>; a fully GitHub-deployed pipeline (no Fabric required) lives at <https://github.com/djouallah/analytics-as-code>. This repo is the Iceberg variant — it writes to the OneLake Iceberg REST catalog, which is what enables Power BI Direct Lake via OneLake's Iceberg→Delta virtualization.

**Contents:** [The data](#the-data) · [OneLake connection](#onelake-connection) · [Prerequisites](#prerequisites) · [dbt Iceberg configuration](#dbt-iceberg-configuration) · [Schema layout](#schema-layout) · [Manual deploy](#manual-deploy-from-laptop) · [Automated deployment](#optional-automated-deployment-to-fabric) · [Limitations](#limitations) · [License](#license)

## The data

AEMO (the Australian Energy Market Operator) publishes the National Electricity Market's dispatch data as free CSVs on [nemweb.com.au](https://www.nemweb.com.au/) — 5-minute SCADA generation per unit (DUID) and regional spot prices. This pipeline downloads those files, lands them as Iceberg tables in OneLake, and serves a summary to a Power BI Direct Lake semantic model.

## OneLake connection

![OneLake explorer showing the data lakehouse with mart schema tables and dbt project files](onelake.png)

Concretely, for OneLake:
- `ENDPOINT` = `https://onelake.table.fabric.microsoft.com/iceberg`
- `WAREHOUSE_PATH` = `{workspace_id}/{lakehouse_id}` — both GUIDs, no `.Lakehouse` suffix
- `FOLDER_PATH` = `abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com/{lakehouse_id}/Files`
- `TOKEN` = bearer token from `notebookutils.credentials.getToken('storage')` (in Fabric) or `az login --scope https://storage.azure.com/.default` (locally — `AzureCliCredential` picks it up automatically, no secrets to manage)

Use the IDs, not the names. With friendly names in the URLs, OneLake's auto-generated Delta metadata silently doesn't get produced for the written tables — probably a temporary bug, but GUIDs sidestep it entirely. `deploy.py` hardcodes the workspace GUID and resolves the display name at deploy time via `duckrun.workspace(ws).display_name`. Inside Fabric the notebook resolves the lakehouse GUID via `notebookutils.lakehouse.get(lakehouse_name).id`; outside Fabric the notebook hardcodes both GUIDs directly.

You can run the notebook anywhere — I've used it on my laptop, GitHub, Colab (why not) — but running inside Fabric just gives you in-region latency, no egress, a scheduler, and automatic token handling.

## Prerequisites

- A Fabric workspace with access to the **OneLake Iceberg write private preview** (the hard gate — everything else is commodity)
- Python 3.11+
- Laptop path: the [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) (`az login` with your own identity)
- CI path: an Azure AD app registration with an OIDC federated credential + two GitHub secrets (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID` — see [CI/CD setup](#cicd-setup-github-actions))

## dbt Iceberg configuration

In `profiles.yml`, the target attaches OneLake as an Iceberg catalog. The `azure`, `iceberg`, and `avro` extensions auto-install + auto-load from the **stable** core repo on first use (the AZURE secret and the iceberg `attach` trigger it) — no `extensions:` list, no `core_nightly`:

```yaml
settings:
  preserve_insertion_order: false
secrets:
  - type: azure
    name: onelake_storage
    provider: access_token
    access_token: "{{ env_var('ONELAKE_TOKEN') }}"
attach:
  - path: "{{ env_var('WAREHOUSE_PATH') }}"
    alias: onelake
    type: iceberg
    options:
      endpoint: "{{ env_var('ONELAKE_ENDPOINT') }}"
      token: "{{ env_var('ONELAKE_TOKEN') }}"
      access_delegation_mode: 'none'
      stage_create_tables: 0
      skip_create_table_metadata_updates: 1
      default_schema: dbo
```

## Schema layout

- **`landing`** — staging and incremental fact tables (source ingestion, deduplication): `fct_scada`, `fct_price`
- **`mart`** — Power BI-facing models (joined, aggregated, ready for Direct Lake): `dim_duid`, `fct_summary`

---

## Manual deploy from laptop

```bash
az login
pip install duckrun
python deploy.py
```

All you need:
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) — `az login` uses your own identity, whatever access you have in Fabric is what the deploy gets
- [`duckrun`](https://pypi.org/project/duckrun/) (`pip install duckrun`) — the deploy library; it self-acquires its own Fabric/OneLake tokens

No service principal, no app registration, no secrets — that whole song-and-dance is only for the CI path below.

## Optional: automated deployment to Fabric

Everything below is opt-in. The core dbt + DuckDB + OneLake loop runs without any of it. This section covers using the included `deploy.py` + GitHub Actions to provision a Lakehouse, push the notebook, run dbt on a schedule, and refresh a Power BI semantic model.

### Architecture

```
GitHub Push
    │
    ▼
GitHub Actions CI
    ├── duckrun    → provision Lakehouse (with schemas)
    ├── dbt run    (DuckDB + OneLake Iceberg)
    ├── dbt test   (validates Iceberg table row counts)
    └── dbt docs   → GitHub Pages
    │
    ▼ (main only)
deploy.py (duckrun)
    ├── Copy dbt/        → OneLake Files
    └── ws.deploy(...)   → Variable Library, Notebook, Semantic Model
        │                  (Direct Lake bim rebound + refreshed), Data Pipeline
        │                  (notebook activity repointed)
        └── ws.schedule → cron schedule on the pipeline
```

![Fabric workspace after deploy: semantic model, lakehouse, variable library, notebook, and pipeline](items.png)

### Stack

| Layer | Tool |
|-------|------|
| Transformations | dbt-core + dbt-duckdb |
| Iceberg catalog | OneLake REST catalog (native) |
| Execution | Python notebook (Fabric) |
| Storage | OneLake (Iceberg / Parquet) |
| Serving | Direct Lake semantic model (Power BI) |
| CI | GitHub Actions |
| Deploy | `duckrun` |

### Environments

| Context | Token source | Use case |
|---------|--------------|----------|
| Local | `az login` (AzureCliCredential) | Local development |
| GitHub Actions | OIDC federated credential (no stored secret) | CI + deploy on `main` |
| Fabric notebook | `notebookutils` | Scheduled pipeline run |

### Configuration files

- `deploy.py` — hardcoded workspace/lakehouse constants + the duckrun deploy flow
- `profiles.yml` — dbt target with Iceberg attach config
- `dbt_project.yml` — model config and variable defaults

### CI/CD setup (GitHub Actions)

Auth is **OIDC** — no long-lived bearer tokens stored in GitHub. The workflow exchanges GitHub's short-lived OIDC token for an Azure AD federated credential via `azure/login@v2`, then mints OneLake storage tokens at runtime with `az account get-access-token`. Tokens live only for the duration of a job.

The only GitHub secrets you need:
- `AZURE_CLIENT_ID` — your Azure AD app registration
- `AZURE_TENANT_ID` — your tenant

On the Azure side, register an app and add a **federated credential** with subject `repo:<owner>/<repo>:ref:refs/heads/main`. Grant it the Fabric workspace permissions you need.

Every branch runs the dbt build (run/test/docs) and publishes docs to GitHub Pages. Only pushes to `main` deploy the Fabric items (`deploy.py` is hardcoded to one workspace).

---

## Limitations

DuckDB's Iceberg **writer** is new and under heavy development — it gained full DML only recently and more is landing in **DuckDB 2.0**. Treat everything below as "true today, likely to improve soon", not as permanent design constraints.

### DuckDB Iceberg writer

Full DML works as of **v1.5.3**: `CREATE TABLE`, `CTAS`, `INSERT`, `UPDATE`, `DELETE`, `MERGE INTO`, and `ALTER TABLE` (schema evolution — add/drop/rename/retype columns), plus `bucket`/`truncate` partition transforms and Iceberg V3. The remaining caveats:

- **Merge-on-read only.** `UPDATE`/`DELETE`/`MERGE` write positional delete files; copy-on-write is not supported. A table whose `write.update.mode` or `write.delete.mode` is set to anything other than `merge-on-read` will fail the operation. Delete files accumulate, so periodic maintenance matters. The biggest practical gap: no `INSERT OVERWRITE` / cheap table replace yet — [duckdb-iceberg#1178](https://github.com/duckdb/duckdb-iceberg/pull/1178) (metadata-only whole-file deletes, aligned with the INSERT OVERWRITE requests [#620](https://github.com/duckdb/duckdb-iceberg/issues/620)/[#826](https://github.com/duckdb/duckdb-iceberg/issues/826)) is the one to watch. Until then, rebuild-style models are best written as **insert-only merges** (`WHEN MATCHED DO NOTHING`), which also sidesteps OneLake's one-add-snapshot-per-commit rule (an update merge = data files + delete files in one commit → `BadRequest 400`).
- **No built-in table maintenance yet.** No compaction / `OPTIMIZE` / snapshot expiry from DuckDB today — handle it out-of-band (PyIceberg does snapshot expiration). Initial support lands in DuckDB 2.0.
- **Partitioned tables** don't honor `write.target-file-size-bytes` or `write.parquet.row-group-size-bytes`.
- **`Geography` and `Unknown` types** aren't supported yet — planned for DuckDB 2.0.
- **Track a recent DuckDB.** Iceberg writes to OneLake work out of the box from **1.4.5**; `MERGE INTO`/`ALTER TABLE` arrived in **1.5.3** (this repo requires `duckdb>=1.5.4`). The extension is under heavy development, so run the latest release rather than pinning a version.

### OneLake / Fabric round-trip

- **Use GUIDs in OneLake URLs, not friendly names.** With names, OneLake silently doesn't produce Delta metadata for the tables you write — probably a temporary bug; GUIDs work around it today.
- **One Iceberg table per OneLake folder.** You can't get Delta if two Iceberg tables share a location — the spec allows it, but OneLake's virtualization identifies tables by directory.
- **Emit `timestamptz`, not `timestamp`.** Naive `TIMESTAMP` maps to Delta `timestamp_ntz`, which Microsoft docs flag as "not fully supported across Fabric workloads." `CAST(... AS TIMESTAMPTZ)` at output columns.
- **Power BI needs Delta metadata, not Iceberg.** Direct Lake can't read Iceberg metadata directly — OneLake auto-generates Delta metadata from the Iceberg tables (that virtualization is the whole reason this pipeline works). Generation is asynchronous, so freshly written data may take a moment to surface for Direct Lake reads.

---

## License

MIT — see [LICENSE](LICENSE).
