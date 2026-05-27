# ⚠️ HIGHLY EXPERIMENTAL — MAY KILL A DUCK 🦆💀

> [!CAUTION]
> **This is not for production systems. Experimental and educational purposes only.**
>
> _Also requires OneLake Iceberg write (private preview, limited access)._
> 
> OneLake Iceberg write is mainly for third-party interoperability — think Snowflake, etc.

---

# dbt + DuckDB + OneLake Iceberg REST Catalog

Iceberg is cool. The whole pipeline runs anywhere Python runs — your laptop, a GitHub Actions runner, a container, an AI agent. I have a full working pipeline already here, fully deployed on GitHub: <https://github.com/djouallah/analytics-as-code>. This repo uses OneLake Catalog for data storage, specifically to enable Power BI semantic model deployment.

Concretely, for OneLake:
- `ENDPOINT` = `https://onelake.table.fabric.microsoft.com/iceberg`
- `WAREHOUSE_PATH` = `{workspace_id}/{lakehouse_id}` — both GUIDs, no `.Lakehouse` suffix
- `FOLDER_PATH` = `abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com/{lakehouse_id}/Files`
- `TOKEN` = bearer token from `notebookutils.credentials.getToken('storage')` (in Fabric) or `az login --scope https://storage.azure.com/.default` (locally — `AzureCliCredential` picks it up automatically, no secrets to manage)

Use the IDs, not the names. With friendly names in the URLs, OneLake's auto-generated Delta metadata silently doesn't get produced for the written tables — probably a temporary bug, but GUIDs sidestep it entirely. `deploy_config.yml` stores the workspace GUID under `ws`; the workspace display name is resolved at deploy time via `fab api -X get workspaces/{ws}`. Inside Fabric the notebook resolves the lakehouse GUID via `notebookutils.lakehouse.get(lakehouse_name).id`; outside Fabric the `local` section of `deploy_config.yml` holds both GUIDs directly.

You can run the notebook anywhere — I've used it on my laptop, GitHub, Colab (why not) — but running inside Fabric just gives you in-region latency, no egress, a scheduler, and automatic token handling.


### Limitations

- **Direct Lake reads** work via OneLake's auto-generated Delta metadata, but generation is asynchronous, so freshly written data may take a moment to appear as a Delta table.
- **Table maintenance is on you** — compaction, snapshot expiry, etc. PyIceberg is a reasonable place to start.
- **DuckDB 1.4.4** specifically — other versions will not work against the OneLake catalog.
- **Force install `avro` from `core_nightly`**.
- **Must set `support_stage_create: 0`** in the Iceberg attach options.

## dbt Iceberg configuration

In `profiles.yml`, all targets attach OneLake as an Iceberg catalog:

```yaml
settings:
  preserve_insertion_order: false
precommand:
  - "FORCE INSTALL iceberg FROM core_nightly"
  - "FORCE INSTALL azure FROM core_nightly"
  - "FORCE INSTALL avro FROM core_nightly"
  - "CREATE OR REPLACE SECRET onelake_storage (TYPE AZURE, PROVIDER ACCESS_TOKEN, ACCESS_TOKEN '{{ env_var('ONELAKE_TOKEN') }}')"
attach:
  - path: "{{ env_var('WAREHOUSE_PATH') }}"
    alias: onelake
    type: iceberg
    options:
      endpoint: "{{ env_var('ONELAKE_ENDPOINT') }}"
      token: "{{ env_var('ONELAKE_TOKEN') }}"
      support_stage_create: 0
      default_schema: dbo
```

## Schema layout

- **`landing`** — staging and incremental fact tables (source ingestion, deduplication): `fct_scada`, `fct_price`
- **`mart`** — Power BI-facing models (joined, aggregated, ready for Direct Lake): `dim_duid`, `fct_summary`

---

## Manual deploy from laptop using Fabric CLI

```bash
az login
python deploy.py --env main
```

All you need:
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) — `az login` uses your own identity, whatever access you have in Fabric is what the deploy gets
- [Microsoft Fabric CLI](https://microsoft.github.io/fabric-cli/) (`pip install ms-fabric-cli`)

No service principal, no app registration, no secrets — that whole song-and-dance is only for the CI path below.

## Optional: automated deployment to Fabric

Everything below is opt-in. The core dbt + DuckDB + OneLake loop runs without any of it. This section covers using the included `deploy.py` + GitHub Actions to provision a Lakehouse, push the notebook, run dbt on a schedule, and refresh a Power BI semantic model.

### Architecture

```
GitHub Push
    │
    ▼
GitHub Actions CI
    ├── dbt run   (DuckDB + OneLake Iceberg, test workspace)
    └── dbt test  (validates Iceberg table row counts)
    │
    ▼
deploy.py (Fabric CLI + Power BI API)
    ├── fab create  → Lakehouse (with schemas)
    ├── fab deploy  → Notebook
    ├── Copy dbt/   → OneLake Files
    ├── fab job run → Notebook runs dbt, writes Iceberg tables to OneLake
    ├── fab deploy  → Semantic Model (Direct Lake, GUIDs swapped)
    ├── Power BI API → Refresh semantic model
    └── fab deploy  → Data Pipeline + cron schedule
```

### Stack

| Layer | Tool |
|-------|------|
| Transformations | dbt-core + dbt-duckdb |
| Iceberg catalog | OneLake REST catalog (native) |
| Execution | Python notebook (Fabric) |
| Storage | OneLake (Iceberg / Parquet) |
| Serving | Direct Lake semantic model (Power BI) |
| CI | GitHub Actions |
| Deploy | Fabric CLI (`ms-fabric-cli`) |

### Environments

| Target | Warehouse | Token source | Use case |
|--------|-----------|--------------|----------|
| `dev` | Test workspace on OneLake | `az login` (AzureCliCredential) | Local development |
| `ci` | Test workspace on OneLake | OIDC federated credential (no stored secret) | GitHub Actions |
| `prod` | Prod workspace on OneLake | notebookutils | Fabric notebook |

### Configuration files

- `deploy_config.yml` — workspace ID, schedule, and settings per environment
- `profiles.yml` — dbt targets with Iceberg attach config
- `dbt_project.yml` — model config and variable defaults

### CI/CD setup (GitHub Actions)

Auth is **OIDC** — no long-lived bearer tokens stored in GitHub. The workflow exchanges GitHub's short-lived OIDC token for an Azure AD federated credential via `azure/login@v2`, then mints OneLake storage tokens at runtime with `az account get-access-token`. Tokens live only for the duration of a job.

The only GitHub secrets you need:
- `AZURE_CLIENT_ID` — your Azure AD app registration
- `AZURE_TENANT_ID` — your tenant

On the Azure side, register an app and add a **federated credential** with subject `repo:<owner>/<repo>:ref:refs/heads/main` (and one per deploy branch). Grant it the Fabric workspace permissions you need.

Push to `main` runs CI tests and publishes dbt docs to GitHub Pages. Push to `production` deploys to Fabric.

---

## Learnings

- **Use GUIDs in OneLake URLs, not friendly names.** With names, OneLake's Iceberg→Delta virtualization silently doesn't produce Delta metadata for the tables you write — probably a temporary bug; GUIDs work around it today.
- **Emit `timestamptz`, not `timestamp`.** Naive `TIMESTAMP` maps to Delta `timestamp_ntz`, which Microsoft docs flag as "not fully supported across Fabric workloads." `CAST(... AS TIMESTAMPTZ)` at output columns.
- **No DELETE on iceberg.** duckdb-iceberg only writes position-delete files (merge-on-read), and OneLake's virtualization can't read them. `TRUNCATE` goes through the same codepath. Stick to `materialized='incremental'` + `incremental_strategy='append'`, no DELETE pre_hooks.
- **One iceberg table per OneLake folder.** You can't get Delta if two iceberg tables share the same location — the spec allows it, but it's bad practice anyway.
- **No Python compaction tool yet.** AFAIK PyIceberg only supports snapshot expiration.
