import os
import duckrun

# No hardcoded GUIDs: WS_ID comes from the environment (CI sets it from the repo variable;
# locally, `set WS_ID=...` / `$env:WS_ID=...` before running). Names keep defaults.
WORKSPACE      = os.environ["WS_ID"]
LAKEHOUSE      = os.environ.get("LH_NAME", "data")
FOLDER         = os.environ.get("FOLDER", "aemo")   # workspace folder new items land in
SCHEDULE_EVERY = "720m"          # unused while the Fabric scheduler is off. NOTE: since
                                 # data.yml's hourly cron was removed (analytics-as-code now
                                 # does the unattended loading, into its own lakehouse),
                                 # nothing refreshes THIS lakehouse on a schedule at all —
                                 # re-enable step 4 below if you want one again
DOWNLOAD_LIMIT = "5"             # limits injected into the Variable Library (in-Fabric notebook run)
PROCESS_LIMIT  = "100"

# The download/process limit pair is deliberately sized per context, not shared config:
#   CI workflow (pipeline.yml)  80/80   — cover a 6-hour window of intraday files
#   here -> Variable Library     5/100  — scheduled Fabric runs top up between CI runs
#   variables.json               1/2    — committed placeholders, overwritten by this deploy
#   notebook local-dev branch    2/1000 — laptop runs (VariableLibrary unavailable)
# WORKSPACE and pipeline.yml's WS_ID both resolve from the same repo variable — one source of truth.

os.chdir(os.path.dirname(os.path.abspath(__file__)))
ws = duckrun.workspace(WORKSPACE)
print(f"Workspace: {ws.display_name} ({WORKSPACE})")

# 1. Lakehouse (idempotent, schema-enabled)
print("[1/4] Ensuring lakehouse...")
ws.create_lakehouse(LAKEHOUSE, folder=FOLDER)

# 2. Copy the dbt project to OneLake Files (the notebook copies it to /tmp and runs it)
print("[2/4] Copying dbt project to OneLake Files...")
files = duckrun.connect(f"{ws.display_name}/{LAKEHOUSE}.Lakehouse")
files.copy("dbt", "dbt", overwrite=True)

# 3. Deploy Fabric items in dependency order (VariableLibrary -> Notebook -> SemanticModel ->
#    DataPipeline). duckrun rebinds the Direct Lake bim to LAKEHOUSE, repoints the pipeline's
#    notebook activity, injects the variable library, and reframes/refreshes the model.
print("[3/4] Deploying Fabric items...")
ws.deploy("fabric_items", lakehouse=LAKEHOUSE, folder=FOLDER, overwrite=True, variables={
    "download_limit": DOWNLOAD_LIMIT,
    "process_limit":  PROCESS_LIMIT,
    "lakehouse_name": LAKEHOUSE,
    "workspace_id":   ws.id,
})

# 4. Fabric pipeline scheduling — intentionally DISABLED (turned off in the workspace).
#    Deploy must not re-arm it. Since data.yml's cron was removed too, this lakehouse has
#    no scheduled writer at all — refresh it by dispatching data.yml/pipeline.yml, or
#    re-enable the in-Fabric schedule by uncommenting:
# print("[4/4] Scheduling pipeline...")
# ws.schedule("run_pipeline", every=SCHEDULE_EVERY)
print("[4/4] Fabric scheduler intentionally left off (refresh via manual dispatch)")

print("=== Deploy complete ===")
