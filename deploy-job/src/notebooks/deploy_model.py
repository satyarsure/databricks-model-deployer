# Databricks notebook source
# MAGIC %md
# MAGIC # Model Deployer — single-notebook pipeline
# MAGIC Wrapper → Validator → Deployer, mirroring Genesis Workbench's `deploy_model.py`.
# MAGIC 1. **Wrapper**: load each artifact (UC Volume / S3), wrap as an MLflow pyfunc with a
# MAGIC    signature from the input/output schema, register each A/B variant to Unity Catalog.
# MAGIC 2. **Validator**: load each registered pyfunc, smoke-test predict, optional `mlflow.evaluate`.
# MAGIC 3. **Deployer**: create/update the serving endpoint (A/B traffic, compute, scale-to-zero,
# MAGIC    tags, budget policy, inference tables).

# COMMAND ----------
# MAGIC %pip install --quiet mlflow-skinny[databricks] databricks-sdk scikit-learn pandas numpy cloudpickle joblib boto3
dbutils.library.restartPython()

# COMMAND ----------
import json, os, re, traceback
from datetime import timedelta
import pandas as pd
import numpy as np
import mlflow
from mlflow.pyfunc import PythonModel
from mlflow.models import infer_signature
from mlflow.models.signature import ModelSignature
from mlflow.types.schema import Schema, ColSpec
from mlflow.tracking import MlflowClient
from databricks.sdk import WorkspaceClient
from databricks.sdk import errors
from databricks.sdk.service.serving import (
    EndpointCoreConfigInput, ServedEntityInput, ServingModelWorkloadType,
    TrafficConfig, Route, EndpointTag, AiGatewayInferenceTableConfig,
)

# Parameters arrive as notebook widgets (set via notebook_params / job_parameters
# and job base_parameters). Catalog/schema are supplied by the bundle (var.catalog /
# var.schema) so nothing deployment-specific is hardcoded here.
dbutils.widgets.text("deploy_spec", "{}")
dbutils.widgets.text("deployment_id", "-1")
dbutils.widgets.text("catalog", "main")
dbutils.widgets.text("schema", "default")

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
TABLE = f"{CATALOG}.{SCHEMA}.model_deployments"
spec = json.loads(dbutils.widgets.get("deploy_spec"))
deployment_id = int(dbutils.widgets.get("deployment_id"))
try:
    run_id = str(dbutils.notebook.entry_point.getDbutils().notebook().getContext().jobId().get())
except Exception:
    run_id = ""

# COMMAND ----------
def sql_escape(v):
    return "" if v is None else str(v).replace("'", "''")

def merge_status(**cols):
    """Upsert the deployment row keyed on deployment_id."""
    set_pairs, insert_cols, insert_vals = ["target.updated_at = CURRENT_TIMESTAMP()"], ["deployment_id"], [str(deployment_id)]
    for k, v in cols.items():
        if isinstance(v, bool):
            lit = "true" if v else "false"
        elif v is None:
            lit = "NULL"
        else:
            lit = f"'{sql_escape(v)}'"
        set_pairs.append(f"target.{k} = {lit}")
        insert_cols.append(k)
        insert_vals.append(lit)
    insert_cols.append("updated_at"); insert_vals.append("CURRENT_TIMESTAMP()")
    spark.sql(f"""
        MERGE INTO {TABLE} AS target
        USING (SELECT {deployment_id} AS deployment_id) AS source
        ON target.deployment_id = source.deployment_id
        WHEN MATCHED THEN UPDATE SET {", ".join(set_pairs)}
        WHEN NOT MATCHED THEN INSERT ({", ".join(insert_cols)}) VALUES ({", ".join(insert_vals)})
    """)

def sanitize_endpoint(name):
    n = re.sub(r"[^A-Za-z0-9_-]", "_", name)
    return n[:60].strip("_") or "endpoint"

LIFECYCLE_TABLE = f"{CATALOG}.{SCHEMA}.model_lifecycle_events"

def log_event(stage, status, message="", version=""):
    """Append an immutable lifecycle event (full transition history / audit trail)."""
    import time as _t
    try:
        spark.sql(f"""
            INSERT INTO {LIFECYCLE_TABLE}
              (event_id, deployment_id, model_name, uc_full_name, model_version,
               stage, status, message, actor, event_time)
            VALUES ({_t.time_ns()}, {deployment_id},
               '{sql_escape(spec.get("name"))}', '{sql_escape(globals().get("uc_full", ""))}',
               '{sql_escape(version)}', '{sql_escape(stage)}', '{sql_escape(status)}',
               '{sql_escape(message)[:1000]}', '{sql_escape(spec.get("deployed_by"))}',
               CURRENT_TIMESTAMP())
        """)
    except Exception as le:
        print(f"[lifecycle] could not log event: {le}")

# COMMAND ----------
# ---- Record the initial IN_PROGRESS row -------------------------------------
uc = spec.get("uc", {}) or {}
compute = spec.get("compute", {}) or {}
uc_model = uc.get("model")
if not (uc.get("catalog") and uc.get("schema") and uc_model):
    raise ValueError(f"Invalid deploy_spec: missing uc catalog/schema/model. uc={uc}, keys={list(spec.keys())}")
uc_full = f'{uc.get("catalog")}.{uc.get("schema")}.{uc_model}'
endpoint_name = sanitize_endpoint(spec.get("endpoint_name") or f"{uc_model}_endpoint")

merge_status(
    model_name=spec.get("name"), description=spec.get("description"),
    uc_catalog=uc.get("catalog"), uc_schema=uc.get("schema"), uc_model=uc_model, uc_full_name=uc_full,
    experiment_name=spec.get("experiment_name"), eval_dataset=spec.get("eval_dataset"),
    serverless_usage_policy=spec.get("serverless_usage_policy"),
    tags=json.dumps(spec.get("tags", {})),
    compute_type=compute.get("compute_type"), gpu_type=compute.get("gpu_type"),
    compute_size=compute.get("size"), scale_to_zero=bool(compute.get("scale_to_zero", True)),
    artifacts_json=json.dumps(spec.get("artifacts", [])),
    input_schema_json=json.dumps(spec.get("input_schema", [])),
    output_schema_json=json.dumps(spec.get("output_schema", [])),
    endpoint_name=endpoint_name, status="IN_PROGRESS", stage="wrapper",
    deployed_by=spec.get("deployed_by"), run_id=run_id,
)
spark.sql(f"UPDATE {TABLE} SET deployed_date = CURRENT_TIMESTAMP() WHERE deployment_id = {deployment_id} AND deployed_date IS NULL")
log_event("wrapper", "IN_PROGRESS", "deployment submitted")

# COMMAND ----------
# ---- Signature / example helpers --------------------------------------------
TYPE_MAP = {"double": "double", "float": "float", "int": "integer", "integer": "integer",
            "long": "long", "bigint": "long", "string": "string", "str": "string", "text": "string",
            "bool": "boolean", "boolean": "boolean", "datetime": "datetime", "binary": "binary"}
DUMMY = {"double": 0.0, "float": 0.0, "integer": 0, "long": 0, "string": "example",
         "boolean": False, "datetime": pd.Timestamp("2020-01-01"), "binary": b"0"}

def mlflow_type(t):
    return TYPE_MAP.get(str(t).lower(), "double")

def build_signature(inp, outp):
    inputs = Schema([ColSpec(mlflow_type(f["type"]), f["name"]) for f in inp]) if inp else None
    outputs = Schema([ColSpec(mlflow_type(f["type"]), f["name"]) for f in outp]) if outp else None
    return ModelSignature(inputs=inputs, outputs=outputs) if inputs is not None and outputs is not None else None

def build_input_example(inp):
    return pd.DataFrame([{f["name"]: DUMMY.get(mlflow_type(f["type"]), 0.0) for f in inp}]) if inp else None

class WrappedModel(PythonModel):
    """Generic pyfunc wrapper around an external model object (Genesis Workbench GWBModel style)."""
    def __init__(self, model):
        self.model = model
    def predict(self, context, model_input, params=None):
        import pandas as pd, numpy as np
        if isinstance(model_input, (pd.DataFrame, np.ndarray)):
            data = model_input
        elif isinstance(model_input, (list, dict)):
            data = pd.DataFrame(model_input if isinstance(model_input, list) else [model_input])
        else:
            data = model_input
        preds = self.model.predict(data)
        return pd.DataFrame(preds) if isinstance(preds, np.ndarray) else preds

def localize_artifact(artifact):
    atype = (artifact.get("type") or "").lower()
    path = artifact["path"]
    if atype in ("uc_volume", "uc", "volume"):
        return path
    if atype == "s3":
        import boto3
        from urllib.parse import urlparse
        u = urlparse(path)
        local = f"/tmp/{os.path.basename(u.path) or 'artifact'}"
        boto3.client("s3").download_file(u.netloc, u.path.lstrip("/"), local)
        return local
    raise ValueError(f"Unsupported artifact type: {atype}")

def load_model_object(local_path):
    if os.path.isdir(local_path):
        if os.path.exists(os.path.join(local_path, "MLmodel")):
            return mlflow.pyfunc.load_model(local_path)
        raise ValueError(f"Directory {local_path} is not an MLflow model")
    try:
        import joblib
        return joblib.load(local_path)
    except Exception:
        import pickle
        with open(local_path, "rb") as f:
            return pickle.load(f)

# COMMAND ----------
# ---- STAGE 1: Wrapper — wrap + register each variant ------------------------
mlflow.set_registry_uri("databricks-uc")
if spec.get("experiment_name"):
    try:
        mlflow.set_experiment(spec["experiment_name"])
    except Exception as e:
        print(f"set_experiment warning: {e}")

client = MlflowClient()
def latest_version(name):
    return max((int(v.version) for v in client.search_model_versions(f"name = '{name}'")), default=0)

signature = build_signature(spec.get("input_schema", []), spec.get("output_schema", []))
input_example = build_input_example(spec.get("input_schema", []))
artifacts = spec.get("artifacts", [])
variant_versions = []

try:
    for i, art in enumerate(artifacts):
        label = art.get("label") or chr(ord("A") + i)
        print(f"[wrapper] variant {label} <- {art.get('path')}")
        raw_model = load_model_object(localize_artifact(art))
        wrapped = WrappedModel(raw_model)
        sig = signature
        if sig is None and input_example is not None:
            try:
                sig = infer_signature(input_example, wrapped.predict(None, input_example))
            except Exception as e:
                print(f"[wrapper] signature inference skipped: {e}")
        with mlflow.start_run(run_name=f"{spec.get('name')}_{label}"):
            mlflow.pyfunc.log_model(
                artifact_path="model", python_model=wrapped, signature=sig,
                input_example=input_example, registered_model_name=uc_full,
                pip_requirements=["mlflow", "scikit-learn", "pandas", "numpy", "cloudpickle"],
            )
        version = latest_version(uc_full)
        variant_versions.append({"label": label, "version": version,
                                 "traffic_percent": art.get("traffic_percent", 100 // max(len(artifacts), 1))})
        print(f"[wrapper] registered {uc_full} v{version} (variant {label})")
    model_version_str = ",".join(f'{v["label"]}:{v["version"]}' for v in variant_versions)
    merge_status(model_version=model_version_str, stage="wrapped")
    log_event("wrapper", "IN_PROGRESS", f"registered {uc_full}", version=model_version_str)
except Exception as e:
    merge_status(status="FAILED", stage="wrapper", error_message=f"{e}\n{traceback.format_exc()[:1500]}")
    log_event("wrapper", "FAILED", str(e))
    raise

# COMMAND ----------
# ---- STAGE 2: Validator — smoke-test + optional evaluate --------------------
merge_status(status="VALIDATING", stage="validator")
log_event("validator", "VALIDATING", "validating registered model(s)", version=globals().get("model_version_str", ""))
try:
    for v in variant_versions:
        model_uri = f"models:/{uc_full}/{v['version']}"
        model = mlflow.pyfunc.load_model(model_uri)
        if input_example is not None:
            print(f"[validator] {model_uri} smoke test: {str(model.predict(input_example))[:150]}")
        eval_path = spec.get("eval_dataset")
        if eval_path:
            try:
                edf = pd.read_parquet(eval_path) if eval_path.endswith(".parquet") else (
                    pd.read_csv(eval_path) if eval_path.endswith(".csv") else None)
                if edf is not None:
                    out_fields = [f["name"] for f in spec.get("output_schema", [])]
                    target = out_fields[0] if out_fields and out_fields[0] in edf.columns else None
                    if target:
                        mlflow.evaluate(model=model_uri, data=edf, targets=target, model_type="regressor")
                        print("[validator] mlflow.evaluate complete")
                    else:
                        model.predict(edf.head(50)); print("[validator] eval dataset scored")
            except Exception as ee:
                print(f"[validator] evaluation warning (non-fatal): {ee}")
    merge_status(stage="validated")
    log_event("validator", "IN_PROGRESS", "validation passed", version=globals().get("model_version_str", ""))
except Exception as e:
    merge_status(status="FAILED", stage="validator", error_message=f"{e}\n{traceback.format_exc()[:1500]}")
    log_event("validator", "FAILED", str(e))
    raise

# COMMAND ----------
# ---- STAGE 3: Deployer — create/update serving endpoint ---------------------
merge_status(status="DEPLOYING", stage="deployer")
log_event("deployer", "DEPLOYING", "creating/updating serving endpoint", version=globals().get("model_version_str", ""))
try:
    size = str(compute.get("size", "SMALL")).upper()
    if str(compute.get("compute_type", "cpu")).lower() == "gpu":
        workload_type = {"SMALL": "GPU_SMALL", "MEDIUM": "GPU_MEDIUM", "LARGE": "GPU_LARGE"}.get(size, "GPU_SMALL")
        workload_size = "Small"
    else:
        workload_type = "CPU"
        workload_size = {"SMALL": "Small", "MEDIUM": "Medium", "LARGE": "Large"}.get(size, "Small")
    scale_to_zero = bool(compute.get("scale_to_zero", True))

    served_entities, routes = [], []
    for v in variant_versions:
        entity_name = re.sub(r"[^A-Za-z0-9_-]", "_", f"{uc_model}_{v['label']}")[:60]
        served_entities.append(ServedEntityInput(
            entity_name=uc_full, entity_version=str(v["version"]), name=entity_name,
            workload_type=ServingModelWorkloadType(workload_type), workload_size=workload_size,
            scale_to_zero_enabled=scale_to_zero))
        routes.append(Route(served_model_name=entity_name, traffic_percentage=int(v.get("traffic_percent", 0))))
    total = sum(r.traffic_percentage for r in routes)
    if total != 100 and routes:
        routes[0].traffic_percentage += (100 - total)

    tags = [EndpointTag(key="application", value="mlops_model_deployer"),
            EndpointTag(key="deployed_by", value=str(spec.get("deployed_by", "")))]
    if compute.get("gpu_type"):
        tags.append(EndpointTag(key="gpu_type", value=str(compute.get("gpu_type"))))
    for k, val in (spec.get("tags", {}) or {}).items():
        tags.append(EndpointTag(key=str(k), value=str(val)))
    # Real serverless usage policy (budget_policy_id) + description are create-time only.
    budget_policy_id = (spec.get("serverless_usage_policy") or "").strip() or None
    description = spec.get("description") or None

    w = WorkspaceClient()
    config = EndpointCoreConfigInput(name=endpoint_name, served_entities=served_entities,
                                     traffic_config=TrafficConfig(routes=routes))
    try:
        w.serving_endpoints.get(endpoint_name)
        print(f"[deployer] updating {endpoint_name} (note: budget policy/description are set only at first create)")
        w.serving_endpoints.update_config_and_wait(
            name=endpoint_name, served_entities=served_entities,
            traffic_config=TrafficConfig(routes=routes), timeout=timedelta(minutes=60))
    except errors.platform.ResourceDoesNotExist:
        print(f"[deployer] creating {endpoint_name} (budget_policy_id={budget_policy_id})")
        kw = dict(name=endpoint_name, config=config, tags=tags, timeout=timedelta(minutes=60))
        if budget_policy_id:
            kw["budget_policy_id"] = budget_policy_id
        if description:
            kw["description"] = description
        w.serving_endpoints.create_and_wait(**kw)

    try:
        w.serving_endpoints.put_ai_gateway(
            name=endpoint_name,
            inference_table_config=AiGatewayInferenceTableConfig(
                catalog_name=CATALOG, schema_name=SCHEMA,
                table_name_prefix=f"{endpoint_name}_payload", enabled=True))
        print("[deployer] inference tables enabled")
    except Exception as ge:
        print(f"[deployer] AI Gateway warning (non-fatal): {ge}")

    # Lifecycle: mark the active (highest-traffic) version as @champion in UC.
    try:
        primary = max(variant_versions, key=lambda v: int(v.get("traffic_percent", 0)))
        client.set_registered_model_alias(uc_full, "champion", int(primary["version"]))
        print(f"[deployer] set alias @champion -> {uc_full} v{primary['version']}")
    except Exception as ae:
        print(f"[deployer] alias warning (non-fatal): {ae}")

    host = spark.conf.get("spark.databricks.workspaceUrl")
    invoke_url = f"https://{host}/serving-endpoints/{endpoint_name}/invocations"
    merge_status(status="COMPLETE", stage="deployed", invoke_url=invoke_url, error_message=None)
    log_event("deployer", "COMPLETE", invoke_url, version=globals().get("model_version_str", ""))
    print(f"[deployer] COMPLETE: {invoke_url}")
except Exception as e:
    merge_status(status="FAILED", stage="deployer", error_message=f"{e}\n{traceback.format_exc()[:1500]}")
    log_event("deployer", "FAILED", str(e))
    raise
