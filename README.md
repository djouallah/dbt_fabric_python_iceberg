# ⚠️ Experimental 🦆

> [!CAUTION]
> **This is not for production systems. Experimental and educational purposes only.**
>
> _Also requires OneLake Iceberg write (private preview, limited access)._
>
>
> **This repo requires a DuckDB 2.0 alpha — no stable release will do.** Compaction runs
> `iceberg_rewrite_data_files()`, which exists only since `duckdb==1.6.0.dev365`.

---

# dbt + DuckDB + OneLake Iceberg REST Catalog

[![Pipeline](https://github.com/djouallah/dbt_fabric_python_iceberg/actions/workflows/pipeline.yml/badge.svg)](https://github.com/djouallah/dbt_fabric_python_iceberg/actions/workflows/pipeline.yml)
[![dashboard](https://img.shields.io/badge/dashboard-live-brightgreen)](https://djouallah.github.io/dbt_fabric_python_iceberg/)
[![dbt docs](https://img.shields.io/badge/dbt%20docs-live-blue)](https://djouallah.github.io/dbt_fabric_python_iceberg/dag/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Iceberg is cool. The whole pipeline runs anywhere Python runs — your laptop, a GitHub Actions runner, a container, an AI agent. The Delta Lake version of this pipeline lives at <https://github.com/djouallah/dbt_fabric_python_delta>; a fully GitHub-deployed pipeline (no Fabric required) lives at <https://github.com/djouallah/analytics-as-code>. This repo is the Iceberg variant — it writes to the OneLake Iceberg REST catalog, which is what enables Power BI Direct Lake via OneLake's Iceberg→Delta virtualization.

**Contents:** [The data](#the-data) · [OneLake connection](#onelake-connection) · [Prerequisites](#prerequisites) · [dbt Iceberg configuration](#dbt-iceberg-configuration) · [Schema layout](#schema-layout) · [Manual deploy](#manual-deploy-from-laptop) · [Automated deployment](#optional-automated-deployment-to-fabric) · [Limitations](#limitations) · [Why Iceberg is cool though](#why-iceberg-is-cool-though) · [License](#license)

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
- **A DuckDB 2.0 alpha** — `duckdb==1.6.0.dev365`, pinned in [`requirements.txt`](requirements.txt) and the notebook's cell 0. Not optional and not a floor: compaction calls `iceberg_rewrite_data_files()`, which no stable DuckDB has. The 1.6.0 dev builds report themselves as `v2.0.0-alpha`. Bump the pin by hand, and keep both places in step so CI and Fabric run the same build.
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
    └── dashboard + dbt docs → GitHub Pages (docs under /dag)
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

Every branch runs the dbt build (run/test/docs) and publishes the docs to GitHub Pages under `/dag/` (branch-based Pages on `gh-pages`). Dashboard-only changes skip all of that: `dashboard.yml` publishes `dashboard/` to the site root in ~30 seconds. Only pushes to `main` deploy the Fabric items (`deploy.py` is hardcoded to one workspace).

**Data refresh runs hourly.** [`data.yml`](.github/workflows/data.yml) is on `cron: '0 * * * *'` and fetches up to 60 files per feed per run — an hour only publishes ~12 intraday files per feed, so that is headroom, not a workload. A workflow-level concurrency group keeps runs from overlapping. Both limits are overridable on manual dispatch, and two extra dispatch inputs exist for maintenance: `compact_only` (skip the load, just compact) and `min_input_files` (how many data files a table needs before it is worth rewriting; drop it to `2` to force a rewrite). The `compact` job runs after every load and is `continue-on-error` — maintenance never fails the pipeline, and it is deliberately not gated on the load succeeding, since fragmentation is just as real after a failed build.

### Live dashboard (browser-side Iceberg reads)

[`dashboard/index.html`](dashboard/index.html) is a single static page that queries the **Iceberg tables live from OneLake — in the browser, no backend, no data exports**:

- **Sign in with Microsoft** (MSAL popup, an Entra SPA app registration with the delegated
  `Azure Storage / user_impersonation` scope). Viewers need read access to the Fabric workspace —
  the page holds no secrets, only the public client id.
- **DuckDB-WASM + the iceberg extension** attach the OneLake Iceberg REST catalog directly
  (`ATTACH '{ws}/{lh}' (TYPE ICEBERG, ENDPOINT ...)`) with the user's bearer token.
- **One bridge is required**: the DuckDB `azure` extension has no WASM build, so the page installs
  a small network shim inside the DuckDB worker that injects the bearer token on OneLake requests
  and rewrites `abfss://{ws}@onelake.dfs...` to `https://onelake.dfs.../{ws}/` in catalog/manifest
  responses (the two forms are byte-length-identical, so even binary avro rewrites cleanly).
  Data then flows through the WASM-native httpfs path.
- Each date range does **one remote scan into a local temp table**; KPIs, charts, and the
  region/fuel filters are answered locally and instantly after that.
- The **Analyze** card is a free-form SQL box over the attached catalog.

---

## Limitations

DuckDB's Iceberg **writer** is new and under heavy development — it gained full DML only recently and more is landing in **DuckDB 2.0**. Treat everything below as "true today, likely to improve soon", not as permanent design constraints.

### DuckDB Iceberg writer

Full DML works as of **v1.5.3**: `CREATE TABLE`, `CTAS`, `INSERT`, `UPDATE`, `DELETE`, `MERGE INTO`, and `ALTER TABLE` (schema evolution — add/drop/rename/retype columns), plus `bucket`/`truncate` partition transforms and Iceberg V3. The remaining caveats:

- **Merge-on-read only.** `UPDATE`/`DELETE`/`MERGE` write positional delete files; copy-on-write is not supported. A table whose `write.update.mode` or `write.delete.mode` is set to anything other than `merge-on-read` will fail the operation. Delete files accumulate, so periodic maintenance matters. The biggest practical gap: no `INSERT OVERWRITE` / cheap table replace yet — [duckdb-iceberg#1178](https://github.com/duckdb/duckdb-iceberg/pull/1178) (metadata-only whole-file deletes, aligned with the INSERT OVERWRITE requests [#620](https://github.com/duckdb/duckdb-iceberg/issues/620)/[#826](https://github.com/duckdb/duckdb-iceberg/issues/826)) is the one to watch. Until then, rebuild-style models are best written as **insert-only merges** (`WHEN MATCHED DO NOTHING`), which also sidesteps OneLake's one-add-snapshot-per-commit rule (an update merge = data files + delete files in one commit → `BadRequest 400`).
- **Compaction yes, snapshot expiry no.** `iceberg_rewrite_data_files()` folds small data files back together and this repo runs it — see the `compact` job in [`.github/workflows/data.yml`](.github/workflows/data.yml) and [`.github/scripts/compact_iceberg.py`](.github/scripts/compact_iceberg.py). It is only on the DuckDB 1.6.0 dev line (self-identifies as v2.0.0-alpha), which is why `requirements.txt` pins an exact pre-release. Caveats: rewritten files get no manifest-level column statistics, V3 tables and partition spec evolution are unsupported, and there is still **no snapshot expiry** — the pre-compaction files stay in OneLake until something expires them, so reads get faster but storage doesn't shrink. Expiry remains out-of-band (PyIceberg does it).
- **Partitioned tables** don't honor `write.target-file-size-bytes` or `write.parquet.row-group-size-bytes`.
- **`Geography` and `Unknown` types** aren't supported yet — planned for DuckDB 2.0.
- **Requires a DuckDB 2.0 alpha.** Iceberg writes to OneLake work out of the box from **1.4.5** and `MERGE INTO`/`ALTER TABLE` arrived in **1.5.3**, so the *pipeline* runs on stable — but `iceberg_rewrite_data_files()` (compaction) exists only on the **1.6.0 dev** line, which identifies itself as `v2.0.0-alpha`. That is why the pin is an exact pre-release, `duckdb==1.6.0.dev365`, in [`requirements.txt`](requirements.txt) and in the notebook's cell 0, rather than a `>=` floor. The extension binary is keyed to the DuckDB build, so pinning DuckDB pins the iceberg extension too. Nothing floats it: bump the pin by hand, and keep both places in step so CI and Fabric run the same build.

### OneLake / Fabric round-trip

- **Use GUIDs in OneLake URLs, not friendly names.** With names, OneLake silently doesn't produce Delta metadata for the tables you write — probably a temporary bug; GUIDs work around it today.
- **One Iceberg table per OneLake folder.** You can't get Delta if two Iceberg tables share a location — the spec allows it, but OneLake's virtualization identifies tables by directory.
- **Emit `timestamptz`, not `timestamp`.** Naive `TIMESTAMP` maps to Delta `timestamp_ntz`, which Microsoft docs flag as "not fully supported across Fabric workloads." `CAST(... AS TIMESTAMPTZ)` at output columns.
- **Power BI needs Delta metadata, not Iceberg.** Direct Lake can't read Iceberg metadata directly — OneLake auto-generates Delta metadata from the Iceberg tables (that virtualization is the whole reason this pipeline works). Generation is asynchronous, so freshly written data may take a moment to surface for Direct Lake reads.

---

## Why Iceberg is cool though

Every limitation above is a snapshot of a moving target. [`duckdb-iceberg`](https://github.com/duckdb/duckdb-iceberg) had **44 distinct contributors** land **3,294 commits in the last 12 months** (as of July 2026) — that's not a side project, that's a writer being built in the open at full speed. The gaps have names and PR numbers, and they close monthly.

[![commit activity](https://img.shields.io/github/commit-activity/y/duckdb/duckdb-iceberg?label=duckdb-iceberg%20commits%2Fyear)](https://github.com/duckdb/duckdb-iceberg/graphs/contributors)
[![contributors](https://img.shields.io/github/contributors/duckdb/duckdb-iceberg?label=contributors%20all%20time)](https://github.com/duckdb/duckdb-iceberg/graphs/contributors)

## License

MIT — see [LICENSE](LICENSE).
