# Databricks notebook source
# MAGIC %md
# MAGIC # 1 · Prepare
# MAGIC Checks the deployment request before anything is created:
# MAGIC 1. Record the deployment in Lakebase (the app's board shows it right away).
# MAGIC 2. Resolve every artifact (and the evaluation dataset) to a readable `/Volumes` path —
# MAGIC    S3 paths are read through the Unity Catalog external volume that covers them.
# MAGIC 3. Detect each artifact's format (model file / MLflow model folder / code folder) and check it
# MAGIC    matches what the user chose.
# MAGIC 4. Check a model signature will be available (Unity Catalog requires one).
# MAGIC 5. Publish the resolved **plan** for the next steps.
# MAGIC
# MAGIC Timeline stage: **prepare**.

# COMMAND ----------
# MAGIC %run ./_setup

# COMMAND ----------
# MAGIC %md ## 1. Record the deployment

# COMMAND ----------
import json
from model_deployer import artifacts

spec = ctx.spec
if not ctx.uc_full:
    raise ValueError(f"Invalid deploy_spec: missing uc catalog/schema/model. uc={ctx.uc}")
ctx.pg_init()
compute = spec.get("compute", {}) or {}
ctx.merge_status(
    model_name=spec.get("name"), description=spec.get("description"),
    uc_catalog=ctx.uc.get("catalog"), uc_schema=ctx.uc.get("schema"), uc_model=ctx.uc_model,
    uc_full_name=ctx.uc_full, experiment_name=ctx.experiment, eval_dataset=spec.get("eval_dataset"),
    serverless_usage_policy=ctx.policy, tags=json.dumps(spec.get("tags", {})),
    compute_type=compute.get("compute_type"), gpu_type=compute.get("gpu_type"),
    compute_size=compute.get("size"), scale_to_zero=bool(compute.get("scale_to_zero", True)),
    artifacts_json=json.dumps(spec.get("artifacts", [])),
    input_schema_json=json.dumps(spec.get("input_schema", [])),
    output_schema_json=json.dumps(spec.get("output_schema", [])),
    permissions_json=json.dumps(spec.get("permissions", {})),
    contract_mode=artifacts.contract_mode(spec),
    sample_input_json=(spec.get("sample_input") or ""), sample_output_json=(spec.get("sample_output") or ""),
    endpoint_name=ctx.endpoint_name, status="IN_PROGRESS", stage="prepare",
    deployed_by=spec.get("deployed_by"), run_id=ctx.run_id,
)
# Re-running prepare (a retry or a repair run) starts this deployment's timeline over.
ctx.pg_exec(f"DELETE FROM {ctx.pg_schema}.model_lifecycle_events WHERE deployment_id = %s", [ctx.deployment_id])
ctx.log_event("prepare", "IN_PROGRESS", "deployment submitted")

# COMMAND ----------
# MAGIC %md ## 2. Resolve locations (S3 → UC Volume)

# COMMAND ----------
all_variants = spec.get("artifacts", []) or []
new_variants = [a for a in all_variants if a.get("source") != "existing"]

for a in new_variants:
    original_type, original_path = (a.get("type") or "uc_volume").lower(), a.get("path") or ""
    resolved = artifacts.resolve_path(spark, original_type, original_path)
    if resolved != original_path:
        a.update(type="uc_volume", path=resolved, original_type=original_type, original_path=original_path)
        ctx.log_event("prepare", "IN_PROGRESS", f"resolved to UC volume: {original_path} -> {resolved}")

eval_path = (spec.get("eval_dataset") or "").strip()
if eval_path:
    resolved = artifacts.resolve_path(spark, artifacts.eval_dataset_location(spec), eval_path)
    if resolved != eval_path:
        ctx.log_event("prepare", "IN_PROGRESS", f"resolved to UC volume: eval dataset {eval_path} -> {resolved}")
    eval_path = resolved

# COMMAND ----------
# MAGIC %md ## 3. Detect formats · 4. Check a signature will be available

# COMMAND ----------
mode = artifacts.contract_mode(spec)
form_has_signature = artifacts.form_signature(spec) is not None

for i, a in enumerate(new_variants):
    label = a.get("label") or chr(ord("A") + i)
    detected = artifacts.detect_format(a["path"])
    chosen = (a.get("format") or "").lower()
    if chosen and chosen != detected:
        raise ValueError(f"variant {label}: you chose {chosen!r} but {a['path']} is a {detected!r}")
    a["format"] = detected

    if detected == "mlflow_model":
        if not artifacts.mlflow_folder_has_signature(a["path"]) and not form_has_signature:
            raise ValueError(f"{a['path']} has no signature (Unity Catalog requires one): choose a "
                             f"Schema or Sample contract on the form")
    elif mode == "model":
        raise ValueError(f"'Use the model's own signature' only applies to MLflow model folders; "
                         f"variant {label} is a {detected!r} — choose a Schema or Sample contract")
    elif not form_has_signature:
        raise ValueError(f"variant {label} ({detected}) needs a contract: an input/output schema "
                         f"or a sample input + output")

# COMMAND ----------
# MAGIC %md ## 5. Publish the plan

# COMMAND ----------
ctx.set_value("plan", {"artifacts": all_variants, "eval_dataset": eval_path})
ctx.merge_status(artifacts_json=json.dumps(all_variants), eval_dataset=eval_path or None, stage="prepared")
ctx.log_event("prepare", "IN_PROGRESS", "formats: " + (
    ", ".join(f"{a.get('label') or chr(ord('A') + i)}={a['format']}" for i, a in enumerate(new_variants))
    or "existing versions only"))
