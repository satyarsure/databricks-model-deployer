# Databricks notebook source
# MAGIC %md
# MAGIC # 2 · Register
# MAGIC Turns each new variant into a Unity Catalog model version (`artifacts.register_variant`):
# MAGIC - **model file** → wrapped by `FileModel` (the file is logged as an artifact)
# MAGIC - **MLflow model folder** → registered as-is, with its own code, artifacts and environment
# MAGIC - **code folder** → MLflow models-from-code (`model.py` + its artifacts)
# MAGIC
# MAGIC An **existing** variant (A/B champion) is referenced as-is — nothing is registered for it.
# MAGIC
# MAGIC Timeline stage: **wrapper**.

# COMMAND ----------
# MAGIC %run ./_setup

# COMMAND ----------
from model_deployer import artifacts

ctx.use_experiment()
ctx.merge_status(status="IN_PROGRESS", stage="wrapper")

variants = ctx.plan["artifacts"]
variant_versions = []
for i, a in enumerate(variants):
    label = a.get("label") or chr(ord("A") + i)
    traffic = a.get("traffic_percent", 100 // max(len(variants), 1))
    if a.get("source") == "existing":
        version, fmt = int(a["version"]), "existing"
        print(f"[register] variant {label}: existing {ctx.uc_full} v{version}")
    else:
        fmt = a["format"]
        print(f"[register] variant {label} ({fmt}) <- {a['path']}")
        version, notes = artifacts.register_variant(ctx, a, label)
        for note in notes:
            ctx.log_event("wrapper", "IN_PROGRESS", f"variant {label}: {note}", version="")
    variant_versions.append({"label": label, "version": version, "traffic_percent": traffic, "format": fmt})

# COMMAND ----------
# Hand the versions to the next steps and show them on the board.
ctx.set_value("variant_versions", variant_versions)
ctx.version_str = ",".join(f'{v["label"]}:{v["version"]}' for v in variant_versions)
ctx.merge_status(model_version=ctx.version_str, stage="wrapped")
ctx.log_event("wrapper", "IN_PROGRESS", f"registered {ctx.uc_full}")
