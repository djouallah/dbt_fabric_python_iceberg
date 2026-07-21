import os
import duckrun

WORKSPACE      = "ea575278-bd81-459c-9680-47829898c902"   # analytics workspace
LAKEHOUSE      = "data"
FOLDER         = "aemo"          # workspace folder new items land in
SCHEDULE_EVERY = "720m"          # pipeline cadence
DOWNLOAD_LIMIT = "5"             # limits injected into the Variable Library (in-Fabric notebook run)
PROCESS_LIMIT  = "100"

os.chdir(os.path.dirname(os.path.abspath(__file__)))
ws = duckrun.workspace(WORKSPACE)
print(f"Workspace: {ws.display_name} ({WORKSPACE})")

# 1. Lakehouse (idempotent, schema-enabled)
ws.create_lakehouse(LAKEHOUSE, folder=FOLDER)

# 2. Copy the dbt project to OneLake Files (the notebook copies it to /tmp and runs it)
files = duckrun.connect(f"{ws.display_name}/{LAKEHOUSE}.Lakehouse")
files.copy("dbt", "dbt", overwrite=True)

# 3. Deploy Fabric items in dependency order (VariableLibrary -> Notebook -> SemanticModel ->
#    DataPipeline). duckrun rebinds the Direct Lake bim to LAKEHOUSE, repoints the pipeline's
#    notebook activity, injects the variable library, and reframes/refreshes the model.
ws.deploy("fabric_items", lakehouse=LAKEHOUSE, folder=FOLDER, overwrite=True, variables={
    "download_limit": DOWNLOAD_LIMIT,
    "process_limit":  PROCESS_LIMIT,
    "lakehouse_name": LAKEHOUSE,
    "workspace_id":   ws.id,
})

# 4. Schedule the pipeline (idempotent — updates, never stacks duplicates)
ws.schedule("run_pipeline", every=SCHEDULE_EVERY)

print("=== Deploy complete ===")
