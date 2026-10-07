# Databricks notebook source
# MAGIC %md
# MAGIC # On failure
# MAGIC Runs when any task failed (`run_if: AT_LEAST_ONE_FAILED`): finds the failed task, records
# MAGIC FAILED + its stage + error in Lakebase (one place, instead of try/except in every stage), then
# MAGIC re-raises so the job run itself still shows as failed.

# COMMAND ----------
# Make the shared package (src/model_deployer) importable: it sits next to this notebooks folder.
import os, sys
_nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
_src = os.path.dirname(os.path.dirname(_nb if _nb.startswith("/Workspace") else "/Workspace" + _nb))
if _src not in sys.path:
    sys.path.insert(0, _src)
from model_deployer.context import Ctx
ctx = Ctx(dbutils, spark)

# COMMAND ----------
STAGE = {"prepare": "prepare", "register": "wrapper", "validate_in_job": "validator",
         "validate_isolated": "validator", "deploy_endpoint": "deployer", "smoke_test": "smoke_test",
         "finalize": "deployer"}
vv = ctx.get_value("register", "variant_versions", [])
ctx.version_str = ",".join(f'{v["label"]}:{v["version"]}' for v in vv)
failed, err, trace = None, "unknown error", ""
try:
    run = ctx.w.jobs.get_run(int(ctx.run_id))
    for t in run.tasks or []:
        rs = t.state.result_state.value if t.state and t.state.result_state else ""
        if t.task_key != "on_failure" and rs in ("FAILED", "TIMEDOUT", "CANCELED"):
            failed = t
            break
    if failed:
        out = ctx.w.jobs.get_run_output(failed.run_id)
        err = out.error or (failed.state.state_message if failed.state else "") or err
        trace = (out.error_trace or "")[:1500]
except Exception as e:
    print(f"[on_failure] could not inspect the run: {e}")
key = failed.task_key if failed else "unknown"
stage = STAGE.get(key, key)
ctx.merge_status(status="FAILED", stage=stage, error_message=f"{err}\n{trace}")
ctx.log_event(stage, "FAILED", f"{key}: {err}")
raise RuntimeError(f"deployment failed at task '{key}': {err}")
