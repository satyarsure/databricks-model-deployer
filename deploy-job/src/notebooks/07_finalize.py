# Databricks notebook source
# MAGIC %md
# MAGIC # 6 · Finalize
# MAGIC Marks the active (highest-traffic) version **@champion** — only after the smoke test passed —
# MAGIC and records the deployment COMPLETE.

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
import mlflow
from mlflow.tracking import MlflowClient

mlflow.set_registry_uri("databricks-uc")
vv = ctx.get_value("register", "variant_versions", [])
ctx.version_str = ",".join(f'{v["label"]}:{v["version"]}' for v in vv)
try:
    primary = max(vv, key=lambda v: int(v.get("traffic_percent", 0)))
    MlflowClient().set_registered_model_alias(ctx.uc_full, "champion", int(primary["version"]))
    print(f"[finalize] set alias @champion -> {ctx.uc_full} v{primary['version']}")
except Exception as ae:
    print(f"[finalize] alias warning (non-fatal): {ae}")
invoke_url = ctx.get_value("deploy_endpoint", "invoke_url", "")
ctx.merge_status(status="COMPLETE", stage="deployed", invoke_url=invoke_url, error_message=None)
ctx.log_event("deployer", "COMPLETE", invoke_url)
