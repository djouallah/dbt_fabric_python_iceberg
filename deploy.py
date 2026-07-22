import os
import duckrun

WORKSPACE      = "ea575278-bd81-459c-9680-47829898c902"   # analytics workspace
LAKEHOUSE      = "data"
FOLDER         = "aemo"          # workspace folder new items land in
SCHEDULE_EVERY = "720m"          # pipeline cadence — runs alongside the 6-hourly CI cron by
                                 # design (demo of in-Fabric scheduling); overlap safety comes
                                 # from keyed fact merges + the assert_*_grain test tripwires
DOWNLOAD_LIMIT = "5"             # limits injected into the Variable Library (in-Fabric notebook run)
PROCESS_LIMIT  = "100"

# The download/process limit pair is deliberately sized per context, not shared config:
#   CI workflow (pipeline.yml)  80/80   — cover a 6-hour window of intraday files
#   here -> Variable Library     5/100  — scheduled Fabric runs top up between CI runs
#   variables.json               1/2    — committed placeholders, overwritten by this deploy
#   notebook local-dev branch    2/1000 — laptop runs (VariableLibrary unavailable)
# WORKSPACE also appears as WS_ID in pipeline.yml — keep those two in sync.

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

# 4. Schedule the pipeline (idempotent — updates, never stacks duplicates)
print("[4/4] Scheduling pipeline...")
ws.schedule("run_pipeline", every=SCHEDULE_EVERY)

print("=== Deploy complete ===")
