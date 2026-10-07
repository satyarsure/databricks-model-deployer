"""Run context shared by every deploy-job task: parameters, the deploy spec, the plan handed between
tasks (task values), and the Lakebase Postgres writers for the app-facing status + lifecycle tables.

The React app reads model_deployments / model_lifecycle_events from Lakebase (sub-second,
real-time). The job is the WRITER: it owns the schema, creates the tables, upserts the status row,
appends lifecycle events, and grants the app's SP access. Auth is an OAuth DB credential for the
task's own identity (POST /api/2.0/postgres/credentials) — no static secret.
"""
import json
import re
import time

import psycopg
from databricks.sdk import WorkspaceClient

# Every task gets the same widgets. The bundle supplies the defaults (base_parameters); the app's
# run-now notebook_params (deploy_spec, deployment_id) apply to every notebook task.
WIDGETS = {
    "deploy_spec": "{}",
    "deployment_id": "-1",
    # UC location where MODELS are registered (bundle var.catalog / var.schema).
    "catalog": "main",
    "schema": "default",
    # Deploy-time defaults (bundle var.*); the form's values win when given.
    "experiment": "",
    "serverless_policy": "",
    # Lakebase Postgres coordinates (bundle var.pg_*).
    "pg_host": "",
    "pg_database": "databricks_postgres",
    "pg_endpoint": "",
    "pg_schema": "model_deployer",
    "app_sp": "",
    # This job RUN's id ({{job.run_id}}), recorded as run_id and used by on_failure to inspect tasks.
    "job_run_id": "",
}

PREPARE_TASK = "prepare"


def sanitize_endpoint(name):
    n = re.sub(r"[^A-Za-z0-9_-]", "_", name)
    return n[:60].strip("_") or "endpoint"


class Ctx:
    def __init__(self, dbutils, spark):
        self.dbutils, self.spark = dbutils, spark
        for k, v in WIDGETS.items():
            dbutils.widgets.text(k, v)
        self.params = {k: dbutils.widgets.get(k) for k in WIDGETS}
        self.spec = json.loads(self.params["deploy_spec"] or "{}")
        self.deployment_id = int(self.params["deployment_id"])
        self.catalog = self.params["catalog"]
        self.schema = self.params["schema"]
        self.pg_schema = self.params["pg_schema"]
        self.w = WorkspaceClient()
        self.run_id = self._job_run_id()

        uc = self.spec.get("uc") or {}
        self.uc = uc
        self.uc_model = uc.get("model")
        self.uc_full = (f'{uc.get("catalog")}.{uc.get("schema")}.{self.uc_model}'
                        if uc.get("catalog") and uc.get("schema") and self.uc_model else "")
        self.endpoint_name = sanitize_endpoint(self.spec.get("endpoint_name") or f"{self.uc_model}_endpoint")

        # Resolve deploy-time settings: the form value wins. When the form leaves Experiment blank,
        # log to "<configured experiment base>/<model name>"; the policy falls back to the default.
        self.experiment_default = self.params["experiment"].strip()
        self.policy_default = self.params["serverless_policy"].strip()
        exp = (self.spec.get("experiment_name") or "").strip()
        if not exp and self.experiment_default:
            exp = self.experiment_default.rstrip("/") + "/" + (str(self.spec.get("name") or "model").strip() or "model")
        self.experiment = exp
        self.policy = (self.spec.get("serverless_usage_policy") or "").strip() or self.policy_default

        # Comma-joined "label:version" of the registered variants, once the register task has run.
        self.version_str = ""
        self._pg = None
        self._pg_user = None
        self._plan = None

    # ---- identity / job metadata --------------------------------------------------------------
    def _context_attrs(self):
        try:
            return json.loads(
                self.dbutils.notebook.entry_point.getDbutils().notebook().getContext().safeToJson()
            ).get("attributes", {})
        except Exception:
            return {}

    def _job_run_id(self):
        # The job RUN id (not the job id). Comes from the {{job.run_id}} base parameter; falls back
        # to the notebook context when that isn't set (e.g. an interactive run).
        rid = self.params["job_run_id"].strip()
        if rid and not rid.startswith("{{"):
            return rid
        a = self._context_attrs()
        return str(a.get("multitaskParentRunId") or a.get("rootRunId") or a.get("currentRunId") or "")

    def job_tags(self):
        """Governance/chargeback tags = the deploy job's OWN tags (bundle var.resource_tags), read at
        runtime so the tag set is defined in exactly one place. Non-fatal: {} if unreadable."""
        try:
            job_id = self.dbutils.notebook.entry_point.getDbutils().notebook().getContext().jobId().get()
            if not job_id:
                return {}
            return dict(self.w.jobs.get(int(job_id)).settings.tags or {})
        except Exception as e:
            print(f"[tags] could not read deploy-job tags (endpoints get minimal tags): {e}")
            return {}

    def use_experiment(self):
        """Log MLflow runs to the deployment's experiment, creating its parent folder if missing
        (e.g. the configured experiment base was never created or was deleted)."""
        import os
        import mlflow
        mlflow.set_registry_uri("databricks-uc")
        if not self.experiment:
            return
        try:
            self.w.workspace.mkdirs(os.path.dirname(self.experiment.rstrip("/")))
        except Exception as e:
            print(f"[mlflow] could not create the experiment folder: {e}")
        mlflow.set_experiment(self.experiment)

    # ---- values handed between tasks ----------------------------------------------------------
    def set_value(self, key, value):
        """Publish a JSON value for downstream tasks (dbutils.jobs.taskValues; 48 KiB per value)."""
        self.dbutils.jobs.taskValues.set(key=key, value=json.dumps(value))

    def get_value(self, task_key, key, default=None):
        raw = self.dbutils.jobs.taskValues.get(taskKey=task_key, key=key, default="null", debugValue="null")
        val = json.loads(raw) if isinstance(raw, str) else raw
        return default if val is None else val

    @property
    def plan(self):
        """The resolved deployment plan produced by the prepare task (artifacts with resolved paths +
        formats, eval dataset, contract info). Downstream tasks read this instead of the raw spec."""
        if self._plan is None:
            self._plan = self.get_value(PREPARE_TASK, "plan", default={})
        return self._plan

    # ---- Lakebase Postgres ---------------------------------------------------------------------
    def _pg_token(self):
        # Short-lived (~1h) OAuth credential for the caller's identity; used as the PG password.
        return self.w.api_client.do(
            "POST", "/api/2.0/postgres/credentials", body={"endpoint": self.params["pg_endpoint"]}
        )["token"]

    def _pg_conn(self):
        if self._pg is None or self._pg.closed:
            if self._pg_user is None:
                self._pg_user = self.spark.sql("SELECT current_user()").collect()[0][0]  # PG role = identity
            self._pg = psycopg.connect(
                host=self.params["pg_host"], dbname=self.params["pg_database"], user=self._pg_user,
                password=self._pg_token(), sslmode="require", autocommit=True,
            )
        return self._pg

    def pg_exec(self, sql, params=None, fetch=False):
        # One transparent reconnect (covers scale-to-zero wake, dropped idle conns, token refresh).
        last = None
        for _ in range(2):
            try:
                with self._pg_conn().cursor() as cur:
                    cur.execute(sql, params or ())
                    return cur.fetchall() if fetch else None
            except Exception as e:
                last = e
                self._pg = None
        raise last

    def merge_status(self, **cols):
        """Upsert the deployment row keyed on deployment_id (Postgres INSERT ... ON CONFLICT)."""
        keys = list(cols.keys())
        insert_cols = ["deployment_id"] + keys + ["deployed_date", "updated_at"]
        placeholders = ["%s"] + ["%s"] * len(keys) + ["now()", "now()"]
        values = [self.deployment_id] + [cols[k] for k in keys]
        # deployed_date is set once (first insert) and preserved on update; updated_at always bumps.
        update_set = ", ".join([f"{k} = EXCLUDED.{k}" for k in keys] + ["updated_at = now()"])
        self.pg_exec(
            f'INSERT INTO {self.pg_schema}.model_deployments ({", ".join(insert_cols)}) '
            f'VALUES ({", ".join(placeholders)}) '
            f'ON CONFLICT (deployment_id) DO UPDATE SET {update_set}',
            values,
        )

    def log_event(self, stage, status, message="", version=None):
        """Append an immutable lifecycle event (full transition history / audit trail)."""
        try:
            self.pg_exec(
                f'INSERT INTO {self.pg_schema}.model_lifecycle_events '
                f'(event_id, deployment_id, model_name, uc_full_name, model_version, '
                f' stage, status, message, actor, event_time) '
                f'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, now())',
                [time.time_ns(), self.deployment_id, self.spec.get("name"), self.uc_full,
                 self.version_str if version is None else version, stage, status,
                 str(message)[:1000], self.spec.get("deployed_by")],
            )
        except Exception as le:
            print(f"[lifecycle] could not log event: {le}")

    def pg_init(self):
        """Ensure the Postgres schema + tables exist (idempotent, self-provisioning), publish the
        deploy-time defaults the form pre-fills, and grant the app SP. Keep these column lists in
        sync with merge_status()/log_event() and the app's read routes."""
        s = self.pg_schema
        self.pg_exec(f"CREATE SCHEMA IF NOT EXISTS {s}")
        self.pg_exec(f"""
            CREATE TABLE IF NOT EXISTS {s}.model_deployments (
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
        for c in ("permissions_json", "contract_mode", "sample_input_json", "sample_output_json"):
            self.pg_exec(f"ALTER TABLE {s}.model_deployments ADD COLUMN IF NOT EXISTS {c} TEXT")
        self.pg_exec(f"""
            CREATE TABLE IF NOT EXISTS {s}.model_lifecycle_events (
              event_id BIGINT PRIMARY KEY, deployment_id BIGINT, model_name TEXT, uc_full_name TEXT,
              model_version TEXT, stage TEXT, status TEXT, message TEXT, actor TEXT, event_time TIMESTAMPTZ
            )
        """)
        self.pg_exec(f"CREATE INDEX IF NOT EXISTS ix_lifecycle_deployment "
                     f"ON {s}.model_lifecycle_events (deployment_id, event_time)")
        # Saved-but-not-yet-deployed Deploy-form drafts, managed entirely by the APP (per-user).
        self.pg_exec(f"""
            CREATE TABLE IF NOT EXISTS {s}.model_deployment_drafts (
              draft_id BIGINT PRIMARY KEY, owner TEXT, name TEXT, draft_json TEXT, updated_at TIMESTAMPTZ
            )
        """)
        self.pg_exec(f"CREATE INDEX IF NOT EXISTS ix_drafts_owner ON {s}.model_deployment_drafts (owner, updated_at)")
        # Deploy-time defaults so the Deploy form can visibly pre-fill Experiment (as
        # <experiment base>/<model name>) and the Serverless usage policy.
        self.pg_exec(f"""
            CREATE TABLE IF NOT EXISTS {s}.model_deployer_config (
              key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMPTZ
            )
        """)
        for k, v in (("experiment", self.experiment_default), ("serverless_policy", self.policy_default)):
            self.pg_exec(
                f"INSERT INTO {s}.model_deployer_config (key, value, updated_at) VALUES (%s, %s, now()) "
                f"ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                (k, v),
            )
        app_sp = self.params["app_sp"]
        if app_sp:
            # SELECT so the app reads the tables; INSERT so the app server can write the initial
            # "submitted" record at deploy-submit time; full DML on drafts (app-owned).
            for stmt in (
                f'GRANT USAGE ON SCHEMA {s} TO "{app_sp}"',
                f'GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA {s} TO "{app_sp}"',
                f'ALTER DEFAULT PRIVILEGES IN SCHEMA {s} GRANT SELECT, INSERT ON TABLES TO "{app_sp}"',
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON {s}.model_deployment_drafts TO "{app_sp}"',
            ):
                try:
                    self.pg_exec(stmt)
                except Exception as ge:
                    print(f"[pg] grant skipped (app SP role may not exist yet): {ge}")

