# Databricks notebook source
# MAGIC %md
# MAGIC # On failure
# MAGIC Runs only when a step failed (`run_if: AT_LEAST_ONE_FAILED`). Finds the failed step, records
# MAGIC **FAILED** + that step's error in Lakebase (the red event on the app's board), then raises so
# MAGIC the job run itself also shows as failed.

# COMMAND ----------
# MAGIC %run ./_setup

# COMMAND ----------
# Task key -> the stage name shown on the app's timeline.
STAGE = {"prepare": "prepare", "register": "wrapper", "validate": "validator", "deploy": "deployer"}

ctx.version_str = ",".join(f'{v["label"]}:{v["version"]}'
                           for v in ctx.get_value("register", "variant_versions", []))
failed_task, error, trace = "unknown", "unknown error", ""
try:
    for t in ctx.w.jobs.get_run(int(ctx.run_id)).tasks or []:
        result = t.state.result_state.value if t.state and t.state.result_state else ""
        if t.task_key != "on_failure" and result in ("FAILED", "TIMEDOUT", "CANCELED"):
            out = ctx.w.jobs.get_run_output(t.run_id)
            failed_task = t.task_key
            error = out.error or (t.state.state_message or error)
            trace = (out.error_trace or "")[:1500]
            break
except Exception as e:
    print(f"[on_failure] could not inspect the run: {e}")

stage = STAGE.get(failed_task, failed_task)
ctx.merge_status(status="FAILED", stage=stage, error_message=f"{error}\n{trace}")
ctx.log_event(stage, "FAILED", f"{failed_task}: {error}")
raise RuntimeError(f"deployment failed at step '{failed_task}': {error}")
