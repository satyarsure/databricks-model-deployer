# Databricks notebook source
# MAGIC %md
# MAGIC # 3 · Validate
# MAGIC Tests every newly registered variant the **same way, whatever its format**: in the model's
# MAGIC **own environment**, built by `mlflow models predict --env-manager uv` from the model's
# MAGIC requirements, then (optionally) scores the evaluation dataset there too.
# MAGIC
# MAGIC Which input is used to test:
# MAGIC - a **real** input — the form's sample input, or an MLflow model folder's own saved input
# MAGIC   example. If the model can't predict on it, the deployment **fails here**, before serving.
# MAGIC - otherwise a **synthetic** one-row input built from the columnar schema. A failure on it is
# MAGIC   only a warning (a dummy row can't exercise every model).
# MAGIC
# MAGIC Evaluation is always non-fatal. Timeline stage: **validator**.

# COMMAND ----------
# MAGIC %run ./_setup

# COMMAND ----------
import json
import re
import subprocess
import tempfile

import mlflow
import numpy as np
import pandas as pd
from mlflow.models import Model, convert_input_example_to_serving_input
from model_deployer import artifacts

# uv (pinned in requirements.txt) is installed next to this Python; make it visible to mlflow.
os.environ["PATH"] = os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")
ctx.use_experiment()

variant_versions = ctx.get_value("register", "variant_versions", [])
ctx.version_str = ",".join(f'{v["label"]}:{v["version"]}' for v in variant_versions)
ctx.merge_status(status="VALIDATING", stage="validator")


def predict_in_own_environment(model_dir, data):
    """Predict with the model in an environment built from ITS requirements (not this job's), using
    MLflow's `mlflow models predict --env-manager uv`. Output is captured so a failure reports the
    model's actual error (e.g. "ValueError: X has 2 features ...") instead of a generic exit code."""
    work = tempfile.mkdtemp(prefix="pred_")
    request, out = os.path.join(work, "input.json"), os.path.join(work, "predictions.json")
    with open(request, "w") as f:
        f.write(convert_input_example_to_serving_input(data))
    # The model is already downloaded to model_dir, so the command needs no Databricks access. Run it
    # fully isolated: none of this job's MLFLOW_* settings (experiment, tracking server), and a local
    # tracking folder — a fresh process doesn't inherit this notebook's login anyway.
    env = {k: v for k, v in os.environ.items() if not k.startswith("MLFLOW_")}
    env["MLFLOW_TRACKING_URI"] = env["MLFLOW_REGISTRY_URI"] = f"file://{work}/mlruns"
    proc = subprocess.run(
        [sys.executable, "-m", "mlflow", "models", "predict", "--model-uri", model_dir,
         "--input-path", request, "--content-type", "json", "--output-path", out, "--env-manager", "uv"],
        capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        print(proc.stdout[-4000:], proc.stderr[-8000:], sep="\n")   # full detail in the task output
        raise RuntimeError(model_error(proc.stderr + proc.stdout))
    result = json.load(open(out))
    return result.get("predictions", result) if isinstance(result, dict) else result


# MLflow's own wrapper messages around a failed prediction — never the useful part.
WRAPPER_ERRORS = ("Non-zero exit code", "exception occurred while running model prediction")


def model_error(log):
    """The most useful line of a failed prediction: the model's own error, i.e. the last
    'SomethingError: ...' line that isn't one of MLflow's wrapper messages."""
    lines = [l.strip() for l in log.splitlines() if l.strip()]
    errors = [l for l in lines if re.match(r"^[\w.]*(Error|Exception)\b", l)]
    useful = [l for l in errors if not any(w.lower() in l.lower() for w in WRAPPER_ERRORS)]
    return (useful or errors or lines or ["prediction failed (no output)"])[-1][:500]


def test_input(variant, model_dir):
    """(input data, is_real) — see the table at the top of this notebook."""
    if artifacts.sample_input(ctx.spec) is not None:
        return artifacts.sample_input(ctx.spec), True
    if variant["format"] == "mlflow_model":
        try:
            saved = Model.load(model_dir).load_input_example(model_dir)
            if saved is not None:
                return saved, True
        except Exception as e:
            print(f"[validate] no saved input example: {e}")
    return artifacts.schema_example(ctx.spec), False

# COMMAND ----------
# MAGIC %md ## Predict with each new variant in its own environment

# COMMAND ----------
for v in [v for v in variant_versions if v["format"] != "existing"]:
    uri = f"models:/{ctx.uc_full}/{v['version']}"
    ctx.log_event("validator", "VALIDATING", f"variant {v['label']}: building the model's own environment")
    model_dir = mlflow.artifacts.download_artifacts(uri)
    v["model_dir"] = model_dir
    data, real = test_input(v, model_dir)
    if data is None:
        ctx.log_event("validator", "IN_PROGRESS", f"variant {v['label']}: no input to test with — skipped")
        continue
    try:
        preds = predict_in_own_environment(model_dir, data)
        ctx.log_event("validator", "IN_PROGRESS", f"variant {v['label']}: predicted"
                      f"{'' if real else ' (synthetic schema example)'}: {str(preds)[:200]}")
    except Exception as e:
        if real:
            raise RuntimeError(f"variant {v['label']} could not predict on the sample input in its own "
                               f"environment: {e}") from e
        ctx.log_event("validator", "IN_PROGRESS",
                      f"variant {v['label']}: synthetic schema example failed (non-fatal): {str(e)[:260]}")

# COMMAND ----------
# MAGIC %md ## Score the evaluation dataset (optional, non-fatal)
# MAGIC The dataset's columns are the model's features plus a target column named like the first
# MAGIC output-schema field. Integer outputs get classifier metrics, everything else regressor metrics.

# COMMAND ----------
eval_path = ctx.plan.get("eval_dataset")
out_fields = ctx.spec.get("output_schema") or []
target = out_fields[0]["name"] if out_fields else None
model_type = ("classifier" if out_fields and str(out_fields[0].get("type", "")).lower()
              in ("long", "int", "integer", "bigint") else "regressor")

if eval_path:
    for v in [v for v in variant_versions if v.get("model_dir")]:
        try:
            df = pd.read_parquet(eval_path) if eval_path.endswith(".parquet") else pd.read_csv(eval_path)
            if not target or target not in df.columns:
                ctx.log_event("validator", "IN_PROGRESS",
                              f"variant {v['label']}: eval dataset has no '{target}' column — not scored")
                continue
            preds = predict_in_own_environment(v["model_dir"], df.drop(columns=[target]))
            if preds and isinstance(preds[0], dict):   # multi-output model: score the target field
                preds = [p.get(target, next(iter(p.values()))) for p in preds]
            scored = df.drop(columns=[target]).assign(**{target: df[target].values,
                                                         "prediction_": np.asarray(preds).squeeze()})
            mlflow.models.evaluate(data=scored, predictions="prediction_", targets=target, model_type=model_type)
            ctx.log_event("validator", "IN_PROGRESS", f"variant {v['label']}: evaluated ({model_type} metrics)")
        except Exception as e:
            ctx.log_event("validator", "IN_PROGRESS", f"variant {v['label']}: evaluation skipped: {str(e)[:300]}")

ctx.log_event("validator", "IN_PROGRESS", "validation passed")
