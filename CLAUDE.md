# Fabric Deploy — duckrun

Deployment uses the **duckrun** workspace API (`deploy.py`), not the Fabric CLI (`fab`). The whole
flow is a flat, hardcoded script — one workspace, constants at the top, no `deploy_config.yml`.

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

- `pip install duckrun` brings dbt-duckdb, duckdb (>=1.5.4), deltalake, azure-identity, obstore.
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
  `FORCE INSTALL ... FROM core_nightly` anywhere. Requires `duckdb>=1.5.4`.

## Notebook (`fabric_items/run.Notebook`)

Cell 0 installs `dbt-duckdb` + `duckdb>=1.5.4` then `notebookutils.session.restartPython()` — the
Fabric runtime preloads an older duckdb binary, so the restart is required before dbt imports it.
The local-dev branch hardcodes the workspace/lakehouse GUIDs (no `deploy_config.yml`).
