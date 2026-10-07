# Databricks notebook source
# MAGIC %md
# MAGIC # 3b · Validate (isolated environment)
# MAGIC For **MLflow model folder** and **code folder** variants, which bring their own dependencies:
# MAGIC `mlflow.models.predict(..., env_manager="uv")` builds the model's OWN environment from its
# MAGIC requirements and predicts on the sample input (or the model's saved input example). A failure
# MAGIC here fails the deployment before anything is served. Optional evaluation runs the same way.

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
import json, tempfile
import mlflow
import numpy as np
from mlflow.models import Model
from model_deployer.contract import Contract
from model_deployer.formats import ISOLATED_FORMATS
from model_deployer.validation import eval_target, evaluate_predictions, load_eval_dataset

# uv (pinned in requirements.txt) lives next to this Python; make it visible to mlflow.
os.environ["PATH"] = os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")
mlflow.set_registry_uri("databricks-uc")
if ctx.experiment:
    try:
        mlflow.set_experiment(ctx.experiment)
    except Exception as e:
        print(f"set_experiment warning: {e}")
vv = ctx.get_value("register", "variant_versions", [])
ctx.version_str = ",".join(f'{v["label"]}:{v["version"]}' for v in vv)
ctx.merge_status(status="VALIDATING", stage="validator")
contract = Contract(ctx.spec)
eval_path = ctx.plan.get("eval_dataset")


def isolated_predict(model_dir, data):
    out = os.path.join(tempfile.mkdtemp(prefix="pred_"), "predictions.json")
    mlflow.models.predict(model_uri=model_dir, input_data=data, env_manager="uv", output_path=out)
    res = json.load(open(out))
    return res.get("predictions", res) if isinstance(res, dict) else res


for v in [v for v in vv if v["format"] in ISOLATED_FORMATS]:
    uri = f"models:/{ctx.uc_full}/{v['version']}"
    ctx.log_event("validator", "VALIDATING", f"variant {v['label']}: building the model's own environment (uv)")
    local = mlflow.artifacts.download_artifacts(uri)
    # A REAL example (the form's sample, or the model's saved input example) must predict — a failure
    # stops the deployment. A synthetic example built from the columnar schema is only a warning
    # (it can't exercise every model), like the in-job smoke test.
    example, real = contract.sample_input, contract.sample_input is not None
    if example is None:
        try:
            example = Model.load(local).load_input_example(local)
            real = example is not None
        except Exception as e:
            print(f"[validator] no saved input example: {e}")
    if example is None:
        example = contract.input_example   # synthetic, from the schema (None in 'model' mode)
    if example is None:
        ctx.log_event("validator", "IN_PROGRESS",
                      f"variant {v['label']}: no sample input or input example — isolated predict skipped")
    else:
        try:
            preds = isolated_predict(local, example)
            print(f"[validator] {uri} isolated predict: {str(preds)[:200]}")
            ctx.log_event("validator", "IN_PROGRESS",
                          f"variant {v['label']}: predicted in its own environment"
                          f"{'' if real else ' (synthetic schema example)'}: {str(preds)[:200]}")
        except Exception as pe:
            if real:
                raise
            print(f"[validator] isolated predict on the synthetic example failed (non-fatal): {pe}")
            ctx.log_event("validator", "IN_PROGRESS",
                          f"variant {v['label']}: synthetic schema example failed in its own environment "
                          f"(non-fatal): {str(pe)[:260]}")
    if eval_path:
        try:
            edf = load_eval_dataset(eval_path)
            target, model_type = eval_target(ctx.spec, edf) if edf is not None else (None, None)
            if target:
                preds = isolated_predict(local, edf.drop(columns=[target]))
                evaluate_predictions(edf, target, model_type, np.asarray(preds))
                print(f"[validator] mlflow evaluate complete (model_type={model_type})")
        except Exception as ee:
            print(f"[validator] evaluation warning (non-fatal): {ee}")
            ctx.log_event("validator", "IN_PROGRESS", f"variant {v['label']}: evaluation skipped: {str(ee)[:300]}")
ctx.log_event("validator", "IN_PROGRESS", "validation passed (isolated environment)")
