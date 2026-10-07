# Databricks notebook source
# MAGIC %md
# MAGIC # 1 · Prepare
# MAGIC Records the deployment in Lakebase, resolves every artifact (and the evaluation dataset) to a
# MAGIC readable /Volumes path, detects each artifact's format, checks a signature will be available,
# MAGIC and publishes the resolved **plan** + routing flags (task values) for the downstream tasks.

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
import json
from model_deployer.context import resolve_types
from model_deployer.contract import Contract
from model_deployer.formats import FORMAT_HANDLERS, ISOLATED_FORMATS, detect_format, inspect_mlflow_folder
from model_deployer.sources import SOURCE_HANDLERS, eval_dataset_type

spec = ctx.spec
ctx.pg_init()
if not ctx.uc_full:
    raise ValueError(f"Invalid deploy_spec: missing uc catalog/schema/model. uc={ctx.uc}, keys={list(spec.keys())}")
contract = Contract(spec)
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
    contract_mode=contract.mode,
    sample_input_json=(spec.get("sample_input") or ""), sample_output_json=(spec.get("sample_output") or ""),
    endpoint_name=ctx.endpoint_name, status="IN_PROGRESS", stage="prepare",
    deployed_by=spec.get("deployed_by"), run_id=ctx.run_id,
)
# Re-running prepare (a retry or a repair run) starts the timeline over for this deployment.
try:
    ctx.pg_exec(f"DELETE FROM {ctx.pg_schema}.model_lifecycle_events WHERE deployment_id = %s", [ctx.deployment_id])
except Exception as de:
    print(f"[lifecycle] could not reset prior events: {de}")
ctx.log_event("prepare", "IN_PROGRESS", "deployment submitted")

# COMMAND ----------
# ---- Sources: resolve each artifact + the eval dataset to a /Volumes path ----------------------
artifacts = spec.get("artifacts", []) or []
new = [a for a in artifacts if a.get("source") != "existing"]
sources = resolve_types(ctx, "artifact_sources", {(a.get("type") or "").lower() for a in new}, "artifacts")
ev_type = eval_dataset_type(spec)
ev_sources = resolve_types(ctx, "eval_dataset_source", {ev_type} if ev_type else set(), "eval dataset")
unknown = (sources | ev_sources) - set(SOURCE_HANDLERS)
if unknown:
    raise ValueError(f"Unsupported source type(s): {sorted(unknown)} (supported: {sorted(SOURCE_HANDLERS)})")
print(f"[sources] artifacts: {sorted(sources) or ['(existing versions only)']}; eval dataset: {sorted(ev_sources) or ['(none)']}")
notes = []
for a in new:
    t, p = (a.get("type") or "").lower(), a.get("path") or ""
    resolved = SOURCE_HANDLERS[t](spark, p)
    if resolved != p:
        # Keep where it came from; the artifact is now a plain UC Volume artifact.
        a.update(type="uc_volume", path=resolved, original_type=t, original_path=p)
        notes.append(f"{p} -> {resolved}")
ev = (spec.get("eval_dataset") or "").strip()
if ev:
    resolved = SOURCE_HANDLERS[ev_type](spark, ev)
    if resolved != ev:
        notes.append(f"eval dataset {ev} -> {resolved}")
    ev = resolved
if notes:
    ctx.log_event("prepare", "IN_PROGRESS", "resolved to UC volume: " + "; ".join(notes))

# COMMAND ----------
# ---- Formats: what each artifact is (file / MLflow model folder / code folder) ----------------
for i, a in enumerate(new):
    detected = detect_format(a["path"])
    declared = (a.get("format") or "").lower()
    if declared and declared != detected:
        raise ValueError(f"variant {a.get('label') or chr(65 + i)}: declared format {declared!r} but "
                         f"{a['path']} is a {detected!r}")
    a["format"] = detected
formats = resolve_types(ctx, "artifact_formats", {a["format"] for a in new}, "artifacts")
unknown = formats - set(FORMAT_HANDLERS)
if unknown:
    raise ValueError(f"Unsupported artifact format(s): {sorted(unknown)} (supported: {sorted(FORMAT_HANDLERS)})")

# Unity Catalog requires a signature: from the MLflow model itself, or from the form's contract.
for a in new:
    if a["format"] == "mlflow_model":
        info = inspect_mlflow_folder(a["path"])
        print(f"[prepare] {a['path']}: {info}")
        if not info["has_signature"] and not contract.has_form_signature:
            raise ValueError(f"{a['path']} has no signature (Unity Catalog requires one): choose a "
                             f"Schema or Sample contract on the form")
    elif contract.mode == "model":
        raise ValueError(f"'Use the model's signature' only applies to MLflow model folders; "
                         f"{a['path']} is a {a['format']!r} — provide a Schema or Sample contract")
    elif not contract.has_form_signature:
        raise ValueError(f"{a['path']} ({a['format']}) needs a contract: an input/output schema or a sample input + output")

# COMMAND ----------
# ---- Publish the plan + routing flags --------------------------------------------------------
plan = {"artifacts": artifacts, "eval_dataset": ev, "contract_mode": contract.mode}
ctx.set_value("plan", plan)
# String flags read by the job's condition tasks (which validation task(s) run).
dbutils.jobs.taskValues.set(key="has_file", value="true" if any(a["format"] == "file" for a in new) else "false")
dbutils.jobs.taskValues.set(key="has_isolated",
                            value="true" if any(a["format"] in ISOLATED_FORMATS for a in new) else "false")
ctx.merge_status(artifacts_json=json.dumps(artifacts), eval_dataset=ev or None, stage="prepared")
ctx.log_event("prepare", "IN_PROGRESS",
              "formats: " + (", ".join(f"{a.get('label') or chr(65 + i)}={a['format']}" for i, a in enumerate(new))
                             or "existing versions only"))
