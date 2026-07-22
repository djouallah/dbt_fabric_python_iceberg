# ⚠️ Experimental 🦆

> [!CAUTION]
> **This is not for production systems. Experimental and educational purposes only.**
>
> _Also requires OneLake Iceberg write (private preview, limited access)._
> 
> OneLake Iceberg write is mainly for third-party interoperability — think Snowflake, etc.

---

# dbt + DuckDB + OneLake Iceberg REST Catalog

Iceberg is cool. The whole pipeline runs anywhere Python runs — your laptop, a GitHub Actions runner, a container, an AI agent. I have a full working pipeline already here, fully deployed on GitHub: <https://github.com/djouallah/analytics-as-code>. This repo uses OneLake Catalog for data storage, specifically to enable Power BI semantic model deployment.

![OneLake explorer showing the data lakehouse with mart schema tables and dbt project files](onelake.png)

Concretely, for OneLake:
- `ENDPOINT` = `https://onelake.table.fabric.microsoft.com/iceberg`
- `WAREHOUSE_PATH` = `{workspace_id}/{lakehouse_id}` — both GUIDs, no `.Lakehouse` suffix
- `FOLDER_PATH` = `abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com/{lakehouse_id}/Files`
- `TOKEN` = bearer token from `notebookutils.credentials.getToken('storage')` (in Fabric) or `az login --scope https://storage.azure.com/.default` (locally — `AzureCliCredential` picks it up automatically, no secrets to manage)

Use the IDs, not the names. With friendly names in the URLs, OneLake's auto-generated Delta metadata silently doesn't get produced for the written tables — probably a temporary bug, but GUIDs sidestep it entirely. `deploy.py` hardcodes the workspace GUID and resolves the display name at deploy time via `duckrun.workspace(ws).display_name`. Inside Fabric the notebook resolves the lakehouse GUID via `notebookutils.lakehouse.get(lakehouse_name).id`; outside Fabric the notebook hardcodes both GUIDs directly.

You can run the notebook anywhere — I've used it on my laptop, GitHub, Colab (why not) — but running inside Fabric just gives you in-region latency, no egress, a scheduler, and automatic token handling.


## dbt Iceberg configuration

In `profiles.yml`, the target attaches OneLake as an Iceberg catalog. Extensions (`azure`, `iceberg`, `avro`) load from the **stable** core repo — no `core_nightly`:

```yaml
extensions:
  - azure
  - iceberg
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

Full DML works as of **v1.5.3** (May 2026): `CREATE TABLE`, `CTAS`, `INSERT`, `UPDATE`, `DELETE`, `MERGE INTO`, and `ALTER TABLE` (schema evolution — add/drop/rename/retype columns), plus `bucket`/`truncate` partition transforms and Iceberg V3. The remaining caveats:

- **Merge-on-read only.** `UPDATE`/`DELETE`/`MERGE` write positional delete files; copy-on-write is not supported. A table whose `write.update.mode` or `write.delete.mode` is set to anything other than `merge-on-read` will fail the operation. Delete files accumulate, so periodic maintenance matters.
- **No built-in table maintenance.** No compaction / `OPTIMIZE` / snapshot expiry from DuckDB — do it out-of-band (PyIceberg does snapshot expiration today).
- **Writing requires an attached REST catalog.** The path-based `iceberg_scan` interface is read-only.
- **Partitioned tables** don't honor `write.target-file-size-bytes` or `write.parquet.row-group-size-bytes`.
- **`Geography` and `Unknown` types** aren't supported yet — planned for DuckDB 2.0.
- **Needs DuckDB ≥ 1.5.4** for OneLake: the write bug is fixed there, and 1.5.3 is where `MERGE`/`ALTER TABLE` landed. (The pin used to be 1.4.4 with `core_nightly` extensions — no longer needed.)

> This repo still uses `incremental_strategy='append'` with dedup-in-SQL by **choice** — it skips the target-table scan a `merge` does — not because `merge` is unavailable.

### OneLake / Fabric round-trip

- **Use GUIDs in OneLake URLs, not friendly names.** With names, OneLake silently doesn't produce Delta metadata for the tables you write — probably a temporary bug; GUIDs work around it today.
- **One Iceberg table per OneLake folder.** You can't get Delta if two Iceberg tables share a location — the spec allows it, but OneLake's virtualization identifies tables by directory.
- **Emit `timestamptz`, not `timestamp`.** Naive `TIMESTAMP` maps to Delta `timestamp_ntz`, which Microsoft docs flag as "not fully supported across Fabric workloads." `CAST(... AS TIMESTAMPTZ)` at output columns.
- **Delta metadata generation is asynchronous.** Freshly written data may take a moment to surface as a Delta table for Direct Lake reads.
