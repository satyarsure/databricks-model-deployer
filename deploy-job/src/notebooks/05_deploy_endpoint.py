# Databricks notebook source
# MAGIC %md
# MAGIC # 4 · Deploy endpoint
# MAGIC Captures the endpoint's current config (for rollback), then creates/updates it: A/B traffic,
# MAGIC compute, scale-to-zero, tags, budget policy, AI Gateway, access permissions.

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
from model_deployer import serving

vv = ctx.get_value("register", "variant_versions", [])
ctx.version_str = ",".join(f'{v["label"]}:{v["version"]}' for v in vv)
ctx.merge_status(status="DEPLOYING", stage="deployer")
ctx.log_event("deployer", "DEPLOYING", "creating/updating serving endpoint")
previous = serving.capture_config(ctx.w, ctx.endpoint_name)
if previous and serving.serves_versions(ctx, previous, vv):
    # A retry of this task after the update already applied: the "current" config is this run's
    # own, so it is not a rollback target. With no known previous config, a smoke-test failure
    # fails the deployment without rolling back (never restores the wrong config).
    print("[deployer] endpoint already serves this run's versions (task retry); previous config unknown")
    ctx.log_event("deployer", "DEPLOYING", "task retry: endpoint already updated; rollback target unknown")
    previous = None
ctx.set_value("previous_config", previous)
created = serving.deploy(ctx, vv)
ctx.set_value("endpoint_created", created)
host = spark.conf.get("spark.databricks.workspaceUrl")
invoke_url = f"https://{host}/serving-endpoints/{ctx.endpoint_name}/invocations"
ctx.set_value("invoke_url", invoke_url)
ctx.merge_status(invoke_url=invoke_url, stage="deployed")
ctx.log_event("deployer", "DEPLOYING", f"endpoint {'created' if created else 'updated'}; running smoke test")
