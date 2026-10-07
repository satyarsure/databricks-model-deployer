# Databricks notebook source
# MAGIC %md
# MAGIC # 2 · Register
# MAGIC Turns each new variant into a Unity Catalog model version with its format handler
# MAGIC (model file -> FileModel wrapper; MLflow model folder -> registered as-is; code folder ->
# MAGIC models-from-code). Existing versions are referenced as-is.

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
from model_deployer.contract import Contract
from model_deployer.formats import FORMAT_HANDLERS, served_pip_requirements

mlflow.set_registry_uri("databricks-uc")
if ctx.experiment:
    try:
        mlflow.set_experiment(ctx.experiment)
    except Exception as e:
        print(f"set_experiment warning: {e}")
ctx.merge_status(status="IN_PROGRESS", stage="wrapper")
contract = Contract(ctx.spec)
served_reqs = served_pip_requirements()
print("[env] served model pip_requirements (file format):", served_reqs)

artifacts = ctx.plan["artifacts"]
variant_versions = []
for i, a in enumerate(artifacts):
    label = a.get("label") or chr(ord("A") + i)
    traffic = a.get("traffic_percent", 100 // max(len(artifacts), 1))
    if a.get("source") == "existing":
        variant_versions.append({"label": label, "version": int(a.get("version")),
                                 "traffic_percent": traffic, "format": "existing"})
        print(f"[register] variant {label} <- existing {ctx.uc_full} v{a.get('version')}")
        continue
    print(f"[register] variant {label} ({a['format']}) <- {a['path']}")
    version, notes = FORMAT_HANDLERS[a["format"]](ctx, a, label, contract, served_reqs)
    variant_versions.append({"label": label, "version": version, "traffic_percent": traffic, "format": a["format"]})
    print(f"[register] registered {ctx.uc_full} v{version} (variant {label})")
    for n in notes:
        ctx.log_event("wrapper", "IN_PROGRESS", f"variant {label}: {n}", version="")

ctx.version_str = ",".join(f'{v["label"]}:{v["version"]}' for v in variant_versions)
ctx.set_value("variant_versions", variant_versions)
ctx.merge_status(model_version=ctx.version_str, stage="wrapped")
ctx.log_event("wrapper", "IN_PROGRESS", f"registered {ctx.uc_full}")
