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
# MAGIC %md
# MAGIC Dependencies are **not** installed here. They come from the job's pinned serverless
# MAGIC environment (`environment_key: locked` → `requirements.txt`), so package versions are
# MAGIC identical on every run. To change them, edit `deploy-job/requirements.txt` and redeploy.

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
    EndpointCoreConfigInput, ServedEntityInput,
    TrafficConfig, Route, EndpointTag, AiGatewayInferenceTableConfig,
    AiGatewayUsageTrackingConfig,
)
# The workload-type enum was renamed across SDK versions; import defensively so a future
# databricks-sdk bump can't break this import (we pin the version in requirements.txt).
try:
    from databricks.sdk.service.serving import ServingModelWorkloadType
except ImportError:
    from databricks.sdk.service.serving import ServedModelInputWorkloadType as ServingModelWorkloadType

# Parameters arrive as notebook widgets (set via notebook_params / job_parameters
# and job base_parameters). Catalog/schema are supplied by the bundle (var.catalog /
# var.schema) so nothing deployment-specific is hardcoded here.
dbutils.widgets.text("deploy_spec", "{}")
dbutils.widgets.text("deployment_id", "-1")
dbutils.widgets.text("catalog", "main")
dbutils.widgets.text("schema", "default")
# Deploy-time defaults supplied by the bundle (var.*). The form can override experiment and the
# endpoint's serverless policy per deployment; when it leaves them blank, these are used.
# (Governance/chargeback tags are NOT passed here — the notebook reads them from the job's own
# tags at runtime; see _job_tags().)
dbutils.widgets.text("experiment", "")             # default MLflow experiment path
dbutils.widgets.text("serverless_policy", "")      # default endpoint budget/usage policy id
# Lakebase Postgres coordinates for the app-facing store (status + lifecycle tables).
# Supplied by the bundle (var.pg_*) so nothing deployment-specific is hardcoded here.
dbutils.widgets.text("pg_host", "")            # endpoint host
dbutils.widgets.text("pg_database", "databricks_postgres")
dbutils.widgets.text("pg_endpoint", "")        # endpoint resource path (for the DB credential)
dbutils.widgets.text("pg_schema", "model_deployer")
dbutils.widgets.text("app_sp", "")             # app service-principal client id to grant SELECT

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
# Deploy-time defaults (bundle variables). A deployment's form values take precedence; these are
# the fallbacks. Governance/chargeback tags come from the job's own tags (see _job_tags()).
EXPERIMENT_DEFAULT = dbutils.widgets.get("experiment").strip()
SERVERLESS_POLICY_DEFAULT = dbutils.widgets.get("serverless_policy").strip()

def _job_tags():
    """Governance/chargeback tags = the deploy job's OWN tags (bundle var.resource_tags), read at
    runtime so the tag set is defined in exactly one place. Applied to serving endpoints. Non-fatal:
    returns {} if the job id/tags can't be read (endpoints then get only minimal tags)."""
    try:
        # Same job-id accessor the notebook uses for run_id (proven to work when run as a job).
        job_id = dbutils.notebook.entry_point.getDbutils().notebook().getContext().jobId().get()
        if not job_id:
            return {}
        return dict(WorkspaceClient().jobs.get(int(job_id)).settings.tags or {})
    except Exception as e:
        print(f"[tags] could not read deploy-job tags (endpoints get minimal tags): {e}")
        return {}
# Catalog/schema are the UC location where MODELS are registered (unchanged). The app's status +
# lifecycle rows now live in Lakebase Postgres, not Delta.
spec = json.loads(dbutils.widgets.get("deploy_spec"))
deployment_id = int(dbutils.widgets.get("deployment_id"))
# Resolve deploy-time settings: the form value wins, else the bundle default (var.experiment /
# var.budget_policy_id). Recorded on the status row and used by the wrapper/deployer stages.
EXPERIMENT_RESOLVED = (spec.get("experiment_name") or "").strip() or EXPERIMENT_DEFAULT
POLICY_RESOLVED = (spec.get("serverless_usage_policy") or "").strip() or SERVERLESS_POLICY_DEFAULT
try:
    run_id = str(dbutils.notebook.entry_point.getDbutils().notebook().getContext().jobId().get())
except Exception:
    run_id = ""

# Pin the SERVED model's environment to the exact versions in use here, so the serving
# container (which is rebuilt from these requirements — e.g. on scale-to-zero cold starts)
# always matches the versions the artifact was loaded/pickled with. Unpinned requirements
# would let serving pull newer packages over time and fail to load or drift predictions.
import importlib.metadata as _im
def _pin(pkg):
    try:
        return f"{pkg}=={_im.version(pkg)}"
    except Exception:
        return None
SERVED_PIP_REQUIREMENTS = [f"mlflow=={mlflow.__version__}"]
for _p in ["scikit-learn", "pandas", "numpy", "scipy", "cloudpickle", "joblib", "xgboost", "lightgbm"]:
    _r = _pin(_p)
    if _r:
        SERVED_PIP_REQUIREMENTS.append(_r)
print("[env] served model pip_requirements:", SERVED_PIP_REQUIREMENTS)

# COMMAND ----------
# ---- Lakebase Postgres: the app-facing store for deployment status + lifecycle -------------
# The React app reads these two tables from Lakebase (sub-second, real-time) rather than a SQL
# warehouse. This notebook is the WRITER: it owns the schema, creates the tables (below),
# upserts the status row, appends lifecycle events, and grants the app's SP SELECT. Auth is an
# OAuth DB credential for the notebook's own identity (POST /api/2.0/postgres/credentials) — no
# static secret. psycopg is pinned in requirements.txt.
import psycopg

PG_HOST = dbutils.widgets.get("pg_host")
PG_DATABASE = dbutils.widgets.get("pg_database")
PG_ENDPOINT = dbutils.widgets.get("pg_endpoint")
PG_SCHEMA = dbutils.widgets.get("pg_schema")
APP_SP = dbutils.widgets.get("app_sp")

_wsc = WorkspaceClient()
_PG_USER = spark.sql("SELECT current_user()").collect()[0][0]  # PG role = caller's identity
_PG = {"conn": None}

def _pg_token():
    # Short-lived (~1h) OAuth credential for the caller's identity; used as the PG password.
    return _wsc.api_client.do(
        "POST", "/api/2.0/postgres/credentials", body={"endpoint": PG_ENDPOINT}
    )["token"]

def _pg_conn():
    c = _PG["conn"]
    if c is None or c.closed:
        _PG["conn"] = psycopg.connect(
            host=PG_HOST, dbname=PG_DATABASE, user=_PG_USER,
            password=_pg_token(), sslmode="require", autocommit=True,
        )
    return _PG["conn"]

def _pg_exec(sql, params=None, fetch=False):
    # One transparent reconnect (covers scale-to-zero wake, dropped idle conns, token refresh).
    last = None
    for _ in range(2):
        try:
            with _pg_conn().cursor() as cur:
                cur.execute(sql, params or ())
                return cur.fetchall() if fetch else None
        except Exception as e:
            last = e
            _PG["conn"] = None
    raise last

def merge_status(**cols):
    """Upsert the deployment row keyed on deployment_id (Postgres INSERT ... ON CONFLICT)."""
    keys = list(cols.keys())
    insert_cols = ["deployment_id"] + keys + ["deployed_date", "updated_at"]
    placeholders = ["%s"] + ["%s"] * len(keys) + ["now()", "now()"]
    values = [deployment_id] + [cols[k] for k in keys]
    # deployed_date is set once (first insert) and preserved on update; updated_at always bumps.
    update_set = ", ".join([f"{k} = EXCLUDED.{k}" for k in keys] + ["updated_at = now()"])
    _pg_exec(
        f'INSERT INTO {PG_SCHEMA}.model_deployments ({", ".join(insert_cols)}) '
        f'VALUES ({", ".join(placeholders)}) '
        f'ON CONFLICT (deployment_id) DO UPDATE SET {update_set}',
        values,
    )

def sanitize_endpoint(name):
    n = re.sub(r"[^A-Za-z0-9_-]", "_", name)
    return n[:60].strip("_") or "endpoint"

# Serving-endpoint access control. spec["permissions"] (optional) grants principals, grouped
# by level, and is honored exactly as given:
#   {"can_manage": [...], "can_query": [...], "can_view": [...]}
# The person who submitted the deploy from the UI (spec["deployed_by"], captured from the app's
# x-forwarded-email — which may differ from the job's run-as identity) gets CAN_MANAGE by
# DEFAULT, but only when the UI didn't already assign them a level (so listing the submitter
# under can_view/can_query wins). Each principal is classified by shape: contains "@" -> user,
# 36-char UUID -> service principal, otherwise a group. Applied as a PATCH (merge) so the
# endpoint owner and any existing ACLs are preserved. Non-fatal: a failure never fails deploy.
_PERM_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

def apply_endpoint_permissions(endpoint_name):
    try:
        from databricks.sdk.service.serving import (
            ServingEndpointAccessControlRequest as _ACR,
            ServingEndpointPermissionLevel as _PL,
        )
    except Exception as ie:
        print(f"[deployer] permissions skipped (SDK lacks serving permission types): {ie}")
        return
    perms = spec.get("permissions") or {}
    level_by_key = {"can_manage": _PL.CAN_MANAGE, "can_query": _PL.CAN_QUERY, "can_view": _PL.CAN_VIEW}
    rank = {_PL.CAN_VIEW: 1, _PL.CAN_QUERY: 2, _PL.CAN_MANAGE: 3}
    # principal -> level. Apply the UI's explicit permissions FIRST so exactly what was entered is
    # honored (if a principal appears under multiple levels, the highest requested level wins).
    wanted = {}
    for key, lvl in level_by_key.items():
        for p in (perms.get(key) or []):
            p = str(p).strip()
            if not p:
                continue
            if p not in wanted or rank[lvl] > rank[wanted[p]]:
                wanted[p] = lvl
    # The UI submitter gets CAN_MANAGE by DEFAULT — but only if the UI did NOT assign them a level
    # explicitly. So listing the submitter under can_view/can_query is honored, not overridden.
    deployer = (spec.get("deployed_by") or "").strip()
    if deployer and deployer not in wanted:
        wanted[deployer] = _PL.CAN_MANAGE
    if not wanted:
        return
    acl = []
    for p, lvl in wanted.items():
        if "@" in p:
            acl.append(_ACR(user_name=p, permission_level=lvl))
        elif _PERM_UUID.match(p):
            acl.append(_ACR(service_principal_name=p, permission_level=lvl))
        else:
            acl.append(_ACR(group_name=p, permission_level=lvl))
    try:
        ep = _wsc.serving_endpoints.get(endpoint_name)
        _wsc.serving_endpoints.update_permissions(serving_endpoint_id=ep.id, access_control_list=acl)
        print(f"[deployer] applied {len(acl)} endpoint permission(s): "
              + ", ".join(f"{p}={lvl.value}" for p, lvl in wanted.items()))
        log_event("deployer", "IN_PROGRESS", f"applied {len(acl)} endpoint permission(s)",
                  version=globals().get("model_version_str", ""))
    except Exception as pe:
        print(f"[deployer] permissions warning (non-fatal): {pe}")
        log_event("deployer", "IN_PROGRESS", f"permission apply warning: {str(pe)[:200]}",
                  version=globals().get("model_version_str", ""))

# Enable AI Gateway (usage tracking + payload inference tables). Idempotent and self-healing:
#   * If the endpoint already has both enabled, leave it (a re-PUT with the same prefix would hit
#     "table already exists").
#   * The default inference-table prefix can collide with a Delta table left behind by a PRIOR
#     deployment of the same endpoint name (the table survives in UC even if the endpoint was
#     deleted/recreated). On that "already exists" error, retry with a per-deployment prefix so a
#     fresh table is created; if that still fails, fall back to usage-tracking-only. Non-fatal.
def enable_ai_gateway(endpoint_name):
    try:
        cur = _wsc.serving_endpoints.get(endpoint_name).ai_gateway
        if (cur and cur.usage_tracking_config and cur.usage_tracking_config.enabled
                and cur.inference_table_config and cur.inference_table_config.enabled):
            print("[deployer] AI Gateway already enabled; leaving as-is")
            return
    except Exception:
        pass

    def _put(prefix):
        kw = {"name": endpoint_name, "usage_tracking_config": AiGatewayUsageTrackingConfig(enabled=True)}
        if prefix is not None:
            kw["inference_table_config"] = AiGatewayInferenceTableConfig(
                catalog_name=CATALOG, schema_name=SCHEMA, table_name_prefix=prefix, enabled=True)
        _wsc.serving_endpoints.put_ai_gateway(**kw)

    try:
        _put(f"{endpoint_name}_payload")
        print("[deployer] AI Gateway: usage tracking + inference tables enabled")
    except Exception as ge:
        if "already exists" in str(ge).lower():
            alt = f"{endpoint_name}_{deployment_id}"
            try:
                _put(alt)
                print(f"[deployer] AI Gateway enabled with fresh inference-table prefix '{alt}' "
                      f"(default prefix's table already existed from a prior deploy)")
            except Exception as ge2:
                try:
                    _put(None)  # usage tracking only, so at least usage is captured
                    print(f"[deployer] AI Gateway: usage tracking enabled; inference table skipped ({ge2})")
                except Exception as ge3:
                    print(f"[deployer] AI Gateway warning (non-fatal): {ge3}")
        else:
            print(f"[deployer] AI Gateway warning (non-fatal): {ge}")

def log_event(stage, status, message="", version=""):
    """Append an immutable lifecycle event (full transition history / audit trail)."""
    import time as _t
    try:
        _pg_exec(
            f'INSERT INTO {PG_SCHEMA}.model_lifecycle_events '
            f'(event_id, deployment_id, model_name, uc_full_name, model_version, '
            f' stage, status, message, actor, event_time) '
            f'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, now())',
            [_t.time_ns(), deployment_id, spec.get("name"), globals().get("uc_full", ""),
             version, stage, status, str(message)[:1000], spec.get("deployed_by")],
        )
    except Exception as le:
        print(f"[lifecycle] could not log event: {le}")

# COMMAND ----------
# ---- Ensure the Postgres schema + tables exist (idempotent, self-provisioning) ------------
# The notebook owns the schema, so a fresh deployment target needs no manual Postgres DDL. Also
# (best-effort) grants the app's service principal read access — the SP role exists once the app
# has been deployed with its `postgres` resource, so a re-run after that makes the grant stick.
# Keep these column lists in sync with merge_status()/log_event() and the app's read routes.
def _pg_init():
    _pg_exec(f'CREATE SCHEMA IF NOT EXISTS {PG_SCHEMA}')
    _pg_exec(f"""
        CREATE TABLE IF NOT EXISTS {PG_SCHEMA}.model_deployments (
          deployment_id BIGINT PRIMARY KEY, model_name TEXT, description TEXT,
          uc_catalog TEXT, uc_schema TEXT, uc_model TEXT, uc_full_name TEXT,
          model_version TEXT, experiment_name TEXT, eval_dataset TEXT,
          serverless_usage_policy TEXT, tags TEXT,
          compute_type TEXT, gpu_type TEXT, compute_size TEXT, scale_to_zero BOOLEAN,
          artifacts_json TEXT, input_schema_json TEXT, output_schema_json TEXT,
          permissions_json TEXT,
          contract_mode TEXT, sample_input_json TEXT, sample_output_json TEXT,
          endpoint_name TEXT, invoke_url TEXT, status TEXT, stage TEXT, error_message TEXT,
          deployed_by TEXT, deployed_date TIMESTAMPTZ, updated_at TIMESTAMPTZ, run_id TEXT
        )
    """)
    # Migrate tables created before these columns existed (idempotent, no-op once present).
    _pg_exec(f'ALTER TABLE {PG_SCHEMA}.model_deployments ADD COLUMN IF NOT EXISTS permissions_json TEXT')
    for _c in ("contract_mode", "sample_input_json", "sample_output_json"):
        _pg_exec(f'ALTER TABLE {PG_SCHEMA}.model_deployments ADD COLUMN IF NOT EXISTS {_c} TEXT')
    _pg_exec(f"""
        CREATE TABLE IF NOT EXISTS {PG_SCHEMA}.model_lifecycle_events (
          event_id BIGINT PRIMARY KEY, deployment_id BIGINT, model_name TEXT, uc_full_name TEXT,
          model_version TEXT, stage TEXT, status TEXT, message TEXT, actor TEXT, event_time TIMESTAMPTZ
        )
    """)
    _pg_exec(f'CREATE INDEX IF NOT EXISTS ix_lifecycle_deployment '
             f'ON {PG_SCHEMA}.model_lifecycle_events (deployment_id, event_time)')
    # Saved-but-not-yet-deployed Deploy-form drafts. Owned/managed entirely by the APP (the deploy
    # job never writes here), so the app SP gets full DML on this table (below). Per-user via `owner`.
    _pg_exec(f"""
        CREATE TABLE IF NOT EXISTS {PG_SCHEMA}.model_deployment_drafts (
          draft_id BIGINT PRIMARY KEY, owner TEXT, name TEXT, draft_json TEXT, updated_at TIMESTAMPTZ
        )
    """)
    _pg_exec(f'CREATE INDEX IF NOT EXISTS ix_drafts_owner '
             f'ON {PG_SCHEMA}.model_deployment_drafts (owner, updated_at)')
    if APP_SP:
        # SELECT so the app reads the tables; INSERT so the app server can write the initial
        # "submitted" record + lifecycle event at deploy-submit time (before this job cold-starts).
        for stmt in (
            f'GRANT USAGE ON SCHEMA {PG_SCHEMA} TO "{APP_SP}"',
            f'GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA {PG_SCHEMA} TO "{APP_SP}"',
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA {PG_SCHEMA} GRANT SELECT, INSERT ON TABLES TO "{APP_SP}"',
            # Drafts are fully owned by the app (create/update/delete), so grant it UPDATE+DELETE too.
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON {PG_SCHEMA}.model_deployment_drafts TO "{APP_SP}"',
        ):
            try:
                _pg_exec(stmt)
            except Exception as ge:
                print(f"[pg] grant skipped (app SP role may not exist yet): {ge}")

_pg_init()

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
    experiment_name=EXPERIMENT_RESOLVED, eval_dataset=spec.get("eval_dataset"),
    serverless_usage_policy=POLICY_RESOLVED,
    tags=json.dumps(spec.get("tags", {})),
    compute_type=compute.get("compute_type"), gpu_type=compute.get("gpu_type"),
    compute_size=compute.get("size"), scale_to_zero=bool(compute.get("scale_to_zero", True)),
    artifacts_json=json.dumps(spec.get("artifacts", [])),
    input_schema_json=json.dumps(spec.get("input_schema", [])),
    output_schema_json=json.dumps(spec.get("output_schema", [])),
    permissions_json=json.dumps(spec.get("permissions", {})),
    # Record the contract the user provided: 'sample' when a sample input+output was given, else
    # 'schema'. The raw sample JSON is stored so it can be shown/prefilled on a new version.
    contract_mode=("sample" if (str(spec.get("sample_input") or "").strip()
                                 and str(spec.get("sample_output") or "").strip()) else "schema"),
    sample_input_json=(spec.get("sample_input") or ""),
    sample_output_json=(spec.get("sample_output") or ""),
    endpoint_name=endpoint_name, status="IN_PROGRESS", stage="wrapper",
    deployed_by=spec.get("deployed_by"), run_id=run_id,
)
# (deployed_date is set on the first upsert above and preserved on later updates.)
# A serverless task can auto-retry on failure; each attempt re-runs this notebook from the
# top. Clear any lifecycle events from a prior attempt of THIS deployment so the timeline
# shows a single clean run (wrapper -> validator -> deployer) instead of duplicated events.
# (model_deployments is an upsert keyed by deployment_id, so it needs no such reset.)
try:
    _pg_exec(f'DELETE FROM {PG_SCHEMA}.model_lifecycle_events WHERE deployment_id = %s', [deployment_id])
except Exception as de:
    print(f"[lifecycle] could not reset prior events: {de}")
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
        data = model_input
        if isinstance(data, pd.DataFrame):
            # A single string/object column (e.g. a text classifier) must be handed to the model as
            # a 1-D sequence of strings: sklearn text vectorizers iterate a DataFrame over COLUMN
            # NAMES, silently collapsing every row into ONE prediction. Numeric or multi-column
            # frames are left as 2-D (tabular models expect that).
            if data.shape[1] == 1 and data.dtypes.iloc[0] == object:
                data = data.iloc[:, 0].tolist()
        elif isinstance(data, dict):
            data = pd.DataFrame([data])
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            data = pd.DataFrame(data)   # list of records -> DataFrame
        # np.ndarray, or a plain list of scalars/strings (e.g. {"instances": [...]}), passes through.
        preds = self.model.predict(data)
        # Return 1-D predictions FLAT so serving responds {"predictions": [v, v, ...]} — matching a
        # {"instances": [...]} contract — instead of nested records [{"0": v}, ...]. Multi-column
        # (2-D) outputs are returned as a DataFrame so each output field keeps its column.
        arr = np.asarray(preds)
        return arr.tolist() if arr.ndim <= 1 else pd.DataFrame(preds)

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
if EXPERIMENT_RESOLVED:
    try:
        mlflow.set_experiment(EXPERIMENT_RESOLVED)
    except Exception as e:
        print(f"set_experiment warning: {e}")

client = MlflowClient()
def latest_version(name):
    return max((int(v.version) for v in client.search_model_versions(f"name = '{name}'")), default=0)

# ---- Contract: a SAMPLE input/output (preferred when given) OR the columnar schema ----------
# Two ways to define a model's contract:
#   1. Columnar SCHEMA (input_schema/output_schema) -> a tabular (ColSpec) signature.
#   2. A real SAMPLE input + output (JSON) -> infer the signature from the example. A list/array
#      sample yields a TENSOR signature, which is what serves the {"instances": [...]} contract,
#      and the smoke test then runs on the REAL sample instead of a synthetic dummy.
# Users paste the serving envelopes ({"instances"/"inputs": ...} and {"predictions": ...}); unwrap.
def _parse_json_maybe(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    s = str(v).strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None

def _unwrap_input(o):
    if isinstance(o, dict):
        for k in ("instances", "inputs"):
            if k in o:
                return o[k]
        if "dataframe_records" in o:
            return pd.DataFrame(o["dataframe_records"])
        if "dataframe_split" in o:
            ds = o["dataframe_split"]
            return pd.DataFrame(ds.get("data", []), columns=ds.get("columns"))
    return o

def _unwrap_output(o):
    if isinstance(o, dict) and "predictions" in o:
        return o["predictions"]
    return o

_si = _parse_json_maybe(spec.get("sample_input"))
_so = _parse_json_maybe(spec.get("sample_output"))
SAMPLE_INPUT = _unwrap_input(_si) if _si is not None else None
SAMPLE_OUTPUT = _unwrap_output(_so) if _so is not None else None

signature = build_signature(spec.get("input_schema", []), spec.get("output_schema", []))
input_example = build_input_example(spec.get("input_schema", []))
if SAMPLE_INPUT is not None:
    # The real sample drives both the input example and (in the wrapper loop) the inferred signature.
    input_example = SAMPLE_INPUT
    print(f"[wrapper] contract from SAMPLE input/output (schema ignored): {str(SAMPLE_INPUT)[:120]}")
artifacts = spec.get("artifacts", [])
variant_versions = []

try:
    for i, art in enumerate(artifacts):
        label = art.get("label") or chr(ord("A") + i)
        traffic = art.get("traffic_percent", 100 // max(len(artifacts), 1))
        # A/B "champion vs challenger": a variant may reference an already-registered
        # version of this same UC model (the currently-deployed champion) instead of a
        # new artifact. Serve it as-is — no re-wrap, no new version.
        if art.get("source") == "existing":
            version = int(art.get("version"))
            variant_versions.append({"label": label, "version": version, "traffic_percent": traffic})
            print(f"[wrapper] variant {label} <- existing {uc_full} v{version}")
            continue
        print(f"[wrapper] variant {label} <- {art.get('path')}")
        raw_model = load_model_object(localize_artifact(art))
        wrapped = WrappedModel(raw_model)
        sig = signature
        if SAMPLE_INPUT is not None:
            # Infer the signature from the REAL sample. Prefer the model's actual output (so the
            # output type is always correct); fall back to the user-provided sample output.
            try:
                sample_out = wrapped.predict(None, SAMPLE_INPUT)
            except Exception as e:
                print(f"[wrapper] sample predict failed; using provided sample output: {e}")
                sample_out = SAMPLE_OUTPUT
            if sample_out is None:
                sample_out = SAMPLE_OUTPUT
            try:
                sig = infer_signature(SAMPLE_INPUT, sample_out)
            except Exception as e:
                print(f"[wrapper] signature inference from sample failed: {e}")
        elif sig is None and input_example is not None:
            try:
                sig = infer_signature(input_example, wrapped.predict(None, input_example))
            except Exception as e:
                print(f"[wrapper] signature inference skipped: {e}")
        with mlflow.start_run(run_name=f"{spec.get('name')}_{label}"):
            mlflow.pyfunc.log_model(
                artifact_path="model", python_model=wrapped, signature=sig,
                input_example=input_example, registered_model_name=uc_full,
                pip_requirements=SERVED_PIP_REQUIREMENTS,
            )
        version = latest_version(uc_full)
        variant_versions.append({"label": label, "version": version, "traffic_percent": traffic})
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
            # The smoke test is NON-FATAL. It runs the model on a generic dummy example built from
            # the input schema — useful for tabular models, but some models (text/NLP, tensor or
            # JSON-style `{"instances": [...]}` contracts, etc.) can't be exercised by that dummy yet
            # serve perfectly well. So a smoke-test failure is surfaced on the lifecycle timeline and
            # the deployment PROCEEDS to serving, rather than blocking on a schema/example mismatch.
            try:
                print(f"[validator] {model_uri} smoke test: {str(model.predict(input_example))[:150]}")
            except Exception as se:
                print(f"[validator] smoke test failed (non-fatal — proceeding to serving): {se}")
                log_event("validator", "IN_PROGRESS",
                          f"smoke test skipped — input schema may not match the model's real "
                          f"contract; proceeding to serving: {str(se)[:260]}",
                          version=globals().get("model_version_str", ""))
        eval_path = spec.get("eval_dataset")
        if eval_path:
            try:
                edf = pd.read_parquet(eval_path) if eval_path.endswith(".parquet") else (
                    pd.read_csv(eval_path) if eval_path.endswith(".csv") else None)
                if edf is not None:
                    out_fields = spec.get("output_schema", [])
                    target_field = out_fields[0] if out_fields else None
                    target = (target_field["name"] if target_field
                              and target_field["name"] in edf.columns else None)
                    if target:
                        # Integer-typed outputs are class labels → classifier metrics;
                        # everything else → regressor metrics.
                        ftype = str((target_field or {}).get("type", "")).lower()
                        model_type = ("classifier"
                                      if ftype in ("long", "int", "integer", "bigint")
                                      else "regressor")
                        # Score with the model, then evaluate as a static dataset. The pyfunc
                        # wraps single-output predictions as a 1-column DataFrame; squeeze to a
                        # 1-D vector so metric computation doesn't hit a predictions/targets
                        # dimension mismatch (model-driven evaluate fails otherwise).
                        feats = edf.drop(columns=[target])
                        preds = np.asarray(model.predict(feats)).squeeze()
                        edf_eval = feats.copy()
                        edf_eval[target] = edf[target].values
                        edf_eval["prediction_"] = preds
                        mlflow.evaluate(data=edf_eval, predictions="prediction_",
                                        targets=target, model_type=model_type)
                        print(f"[validator] mlflow.evaluate complete (model_type={model_type})")
                    else:
                        model.predict(edf.head(50)); print("[validator] eval dataset scored")
            except Exception as ee:
                # Non-fatal, but surface it on the lifecycle timeline so a skipped/broken
                # evaluation is visible instead of silently swallowed.
                print(f"[validator] evaluation warning (non-fatal): {ee}")
                log_event("validator", "IN_PROGRESS", f"evaluation skipped: {str(ee)[:300]}",
                          version=globals().get("model_version_str", ""))
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

    # Endpoint tags, assembled in PRIORITY order for the serving endpoint's 20-tag limit (over 20,
    # the tags API rejects the whole request, so we keep the first 20). Highest priority first, so
    # the tags that survive are, in order:
    #   (1) the deployment's UI/form tags (spec.tags) — always populated first,
    #   (2) the governance / job tags (bundle var.resource_tags),
    #   (3) the auto-added gpu_type / application / deployed_by.
    # The first writer of a key wins its value + position, so a UI tag overrides a governance tag of
    # the same name. Anything beyond 20 is dropped lowest-priority-first, and logged.
    values, order = {}, []
    def _tag(k, v):
        k = str(k)
        if k not in values:
            values[k] = str(v)
            order.append(k)
    for k, v in (spec.get("tags", {}) or {}).items():   # (1) UI/form tags — highest priority
        _tag(k, v)
    for k, v in _job_tags().items():                     # (2) governance / job tags
        _tag(k, v)
    if compute.get("gpu_type"):                          # (3) auto-added, lowest priority
        _tag("gpu_type", compute.get("gpu_type"))
    _tag("application", "mlops_model_deployer")
    _tag("deployed_by", spec.get("deployed_by", ""))

    MAX_ENDPOINT_TAGS = 20
    if len(order) > MAX_ENDPOINT_TAGS:
        dropped = order[MAX_ENDPOINT_TAGS:]
        order = order[:MAX_ENDPOINT_TAGS]
        print(f"[deployer] endpoint tag limit is {MAX_ENDPOINT_TAGS}; have {len(values)} — "
              f"UI/form tags prioritized; dropping lowest-priority: {dropped}")
    tags = [EndpointTag(key=k, value=values[k]) for k in order]
    # Endpoint tags ARE synced on update (via the tags API, below). budget_policy_id + description
    # remain create-time only — the serving config-update API can't change them post-create.
    budget_policy_id = POLICY_RESOLVED or None
    description = spec.get("description") or None

    w = WorkspaceClient()
    config = EndpointCoreConfigInput(name=endpoint_name, served_entities=served_entities,
                                     traffic_config=TrafficConfig(routes=routes))
    try:
        w.serving_endpoints.get(endpoint_name)
        print(f"[deployer] updating {endpoint_name} (budget policy/description stay as first created)")
        w.serving_endpoints.update_config_and_wait(
            name=endpoint_name, served_entities=served_entities,
            traffic_config=TrafficConfig(routes=routes), timeout=timedelta(minutes=60))
        # A config update does NOT refresh endpoint tags, so sync them explicitly via the tags
        # API: upsert the desired keys (so changed values like env/team take effect) and drop
        # any tags that are no longer provided. Non-fatal if the SDK/endpoint can't patch tags.
        try:
            existing = w.serving_endpoints.get(endpoint_name)
            desired_keys = {t.key for t in tags}
            delete_keys = [t.key for t in (existing.tags or []) if t.key not in desired_keys]
            patch_kw = {"name": endpoint_name, "add_tags": tags}
            if delete_keys:
                patch_kw["delete_tags"] = delete_keys
            w.serving_endpoints.patch(**patch_kw)
            print(f"[deployer] synced endpoint tags ({len(tags)} set, {len(delete_keys)} removed)")
        except Exception as te:
            print(f"[deployer] tag sync warning (non-fatal): {te}")
    except errors.platform.ResourceDoesNotExist:
        print(f"[deployer] creating {endpoint_name} (budget_policy_id={budget_policy_id})")
        # budget_policy_id + description are create-time only. Tags are NOT passed to create — they
        # are applied via the tags API right after (below), so an unusual governance tag key/value
        # can never fail endpoint creation (tagging is non-fatal, matching the update path).
        kw = dict(name=endpoint_name, config=config, timeout=timedelta(minutes=60))
        if budget_policy_id:
            kw["budget_policy_id"] = budget_policy_id
        if description:
            kw["description"] = description
        # Drop any kwargs the installed SDK's create_and_wait doesn't accept, so an SDK
        # version change degrades gracefully (warns) instead of raising TypeError.
        import inspect
        _allowed = set(inspect.signature(w.serving_endpoints.create_and_wait).parameters)
        _dropped = [k for k in kw if k not in _allowed]
        if _dropped:
            print(f"[deployer] WARNING: installed databricks-sdk create_and_wait does not accept "
                  f"{_dropped}; skipping. Pin a version that supports them (deploy-job/requirements.txt).")
            kw = {k: v for k, v in kw.items() if k in _allowed}
        w.serving_endpoints.create_and_wait(**kw)
        try:
            w.serving_endpoints.patch(name=endpoint_name, add_tags=tags)
            print(f"[deployer] applied {len(tags)} endpoint tags")
        except Exception as te:
            print(f"[deployer] endpoint tag apply warning (non-fatal): {te}")

    enable_ai_gateway(endpoint_name)

    # Access control: the UI submitter gets CAN_MANAGE, plus any principals from spec.permissions.
    apply_endpoint_permissions(endpoint_name)

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
