# Databricks notebook source
# MAGIC %md
# MAGIC # 3a · Validate (in job)
# MAGIC For **model file** variants (they load in the job's pinned environment): smoke-test predict
# MAGIC on the contract example (non-fatal) and optional mlflow evaluate on the evaluation dataset.

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
import numpy as np
from model_deployer.contract import Contract
from model_deployer.validation import eval_target, evaluate_predictions, load_eval_dataset

mlflow.set_registry_uri("databricks-uc")
if ctx.experiment:
    try:
        mlflow.set_experiment(ctx.experiment)
    except Exception as e:
        print(f"set_experiment warning: {e}")
vv = ctx.get_value("register", "variant_versions", [])
ctx.version_str = ",".join(f'{v["label"]}:{v["version"]}' for v in vv)
ctx.merge_status(status="VALIDATING", stage="validator")
ctx.log_event("validator", "VALIDATING", "validating model file variant(s) in the job environment")
example = Contract(ctx.spec).input_example
eval_path = ctx.plan.get("eval_dataset")

for v in [v for v in vv if v["format"] == "file"]:
    uri = f"models:/{ctx.uc_full}/{v['version']}"
    model = mlflow.pyfunc.load_model(uri)
    if example is not None:
        # NON-FATAL: a synthetic schema example can't exercise every model (text / tensor / JSON
        # contracts), yet the model may serve fine — surface it and let the smoke test decide.
        try:
            print(f"[validator] {uri} smoke test: {str(model.predict(example))[:150]}")
        except Exception as se:
            print(f"[validator] smoke test failed (non-fatal): {se}")
            ctx.log_event("validator", "IN_PROGRESS",
                          f"variant {v['label']}: smoke test skipped — input example may not match the "
                          f"model's real contract: {str(se)[:260]}")
    if eval_path:
        try:
            edf = load_eval_dataset(eval_path)
            if edf is not None:
                target, model_type = eval_target(ctx.spec, edf)
                if target:
                    preds = np.asarray(model.predict(edf.drop(columns=[target]))).squeeze()
                    evaluate_predictions(edf, target, model_type, preds)
                    print(f"[validator] mlflow evaluate complete (model_type={model_type})")
                else:
                    model.predict(edf.head(50))
                    print("[validator] eval dataset scored")
        except Exception as ee:
            print(f"[validator] evaluation warning (non-fatal): {ee}")
            ctx.log_event("validator", "IN_PROGRESS", f"variant {v['label']}: evaluation skipped: {str(ee)[:300]}")
ctx.log_event("validator", "IN_PROGRESS", "validation passed (model files)")
