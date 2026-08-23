# Fabric Deploy — duckrun

Deployment uses the **duckrun** workspace API (`deploy.py`), not the Fabric CLI (`fab`). The whole
flow is a flat script — one workspace, no `deploy_config.yml`. **No GUIDs are hardcoded anywhere**:
the workspace/lakehouse/tenant/client ids live in GitHub repository **variables** (`WS_ID`, `LH_ID`,
`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`) and reach the code as env vars (deploy.py reads `WS_ID`;
the dashboard fetches a `config.json` that dashboard.yml generates from the same variables).

## deploy.py flow

```python
ws = duckrun.workspace(WORKSPACE)          # resolves display name from the ID; self-acquires tokens
ws.create_lakehouse(LAKEHOUSE, folder=FOLDER)   # idempotent, schema-enabled
duckrun.connect(f"{ws.display_name}/{LAKEHOUSE}.Lakehouse").copy("dbt", "dbt", overwrite=True)
ws.deploy("fabric_items", lakehouse=LAKEHOUSE, folder=FOLDER, overwrite=True, variables={...})
ws.schedule("run_pipeline", every="720m")  # idempotent — updates, never stacks duplicates
```

`ws.deploy("fabric_items", ...)` deploys the folder's items **in dependency order**
(VariableLibrary → Notebook → SemanticModel → DataPipeline) and does everything the old `fab`
script did by hand:

- **Direct Lake bim rebind** — `lakehouse=LAKEHOUSE` rewrites the OneLake workspace/lakehouse GUIDs
  baked into `model.bim` so the model targets the right lakehouse. No `parameter.yml`, no GUID regex.
- **Pipeline notebook repoint** — the pipeline's `TridentNotebook` activity is auto-pointed at the
  folder's single notebook (`notebookId`/`workspaceId` rewritten). No `fab set`.
- **Variable Library injection** — `variables={...}` sets values in `variables.json` at deploy time.
- **Semantic model refresh** — a Direct Lake model is reframed/refreshed after deploy, so `deploy`
  returns only once it is live. (The Delta metadata OneLake auto-generates from the Iceberg tables
  the CI dbt run wrote must exist first — it does, CI runs dbt before deploy.)

`FOLDER` only places items duckrun **creates**; existing items are updated in place and stay where
they already live.

## CI (`.github/workflows/pipeline.yml`)

- `pip install -r requirements.txt` → duckrun (brings dbt-duckdb, deltalake, azure-identity,
  obstore) plus an **exact duckdb pre-release pin**, `duckdb==1.6.0.dev365`. Compaction needs
  `iceberg_rewrite_data_files()`, which is only on the 1.6.0 dev line. Bump it by hand, and keep
  it in step with the notebook's cell 0 so CI and Fabric run the same build.
- Auth is OIDC only: `azure/login` + `az account get-access-token` → `ONELAKE_TOKEN` (the Iceberg
  dbt run reads it). duckrun self-acquires its own tokens. No `fab auth login`, no `ms-fabric-cli`.
- Phase 1 provisions the lakehouse with duckrun and exports `WAREHOUSE_PATH`/`FILES_PATH`/limits.
- Phases 2-4 run dbt (run/test/docs). Phase 5 runs `python deploy.py` **only on `main`**.

## OneLake Iceberg write support

The Iceberg REST ATTACH options live in `dbt/profiles.yml`. The current working set:

```yaml
type: iceberg
options:
  endpoint: "{{ env_var('ONELAKE_ENDPOINT') }}"
  token: "{{ env_var('ONELAKE_TOKEN') }}"
  access_delegation_mode: 'none'
  stage_create_tables: 0
  skip_create_table_metadata_updates: 1
  default_schema: dbo
```

- Plain CTAS works with these options (the earlier "CTAS unsupported" limitation is resolved).
- Use int `0`/`1`, not bool `false`/`true`: dbt-duckdb silently drops boolean-false attach options.
- DuckDB extensions (`azure`, `iceberg`, `avro`) auto-install + auto-load from the **stable** core
  repo on first use (the AZURE secret + iceberg ATTACH trigger it) — no `extensions:` list, no
  `FORCE INSTALL ... FROM core_nightly` anywhere. The extension binary is keyed to the duckdb
  build, so the `duckdb==1.6.0.dev365` pin pins the iceberg extension too.

## Compaction (`.github/scripts/compact_iceberg.py`)

Insert-only merges mean every run leaves another small data file per table and nothing folds
them back. The `compact` job in `data.yml` (`needs: data`, `continue-on-error`) runs
`iceberg_rewrite_data_files()` over the nine tables at 64MiB / `min_input_files=5`, under a
`COMPACT_BUDGET_MINUTES` wall-clock budget, and reports per table to the step summary.

- Ported from `djouallah/analytics-as-code`, which runs the same thing against R2. Differences:
  OneLake brings its own Azure token (`access_delegation_mode 'none'` → the catalog vends
  nothing, so the azure secret authorises the data-file I/O), and the reference's
  `s3_uploader_*` tuning is dropped — abfss writes never touch the S3 uploader.
- `prime()` (`iceberg_metadata()`) before each rewrite works around duckdb-iceberg#1349
  ("No credentials are provided"). It is expensive and it is the only metadata call — leave it
  alone, and don't add more.
- No snapshot expiry exists, so compaction speeds up reads but does not shrink storage.
- `$GITHUB_ENV` doesn't cross jobs, so the job re-mints `ONELAKE_TOKEN` and re-resolves
  `WAREHOUSE_PATH` itself.

## Notebook (`fabric_items/run.Notebook`)

Cell 0 installs `dbt-duckdb` + `duckdb==1.6.0.dev365` then `notebookutils.session.restartPython()` — the
Fabric runtime preloads an older duckdb binary, so the restart is required before dbt imports it.
The local-dev branch reads the workspace/lakehouse GUIDs from `WS_ID`/`LH_ID` env vars (no
`deploy_config.yml`, no hardcoded GUIDs).
