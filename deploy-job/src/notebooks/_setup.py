# Databricks notebook source
# MAGIC %md
# MAGIC # Shared setup
# MAGIC Run at the top of every task notebook with `%run ./_setup`. Makes the shared package
# MAGIC (`src/model_deployer`, next to this folder) importable and builds `ctx` — the job parameters,
# MAGIC the deploy spec, and the Lakebase status / timeline writers (`ctx.merge_status`, `ctx.log_event`).

# COMMAND ----------
import os
import sys

_notebook = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
_src = os.path.dirname(os.path.dirname(_notebook if _notebook.startswith("/Workspace") else "/Workspace" + _notebook))
if _src not in sys.path:
    sys.path.insert(0, _src)

from model_deployer.context import Ctx

ctx = Ctx(dbutils, spark)
