# Deploy job — maintainer guide

`mlops_deploy_model_job` turns one deployment request from the app into a live Model Serving
endpoint. The app starts it with two parameters — `deploy_spec` (the Deploy form as JSON) and
`deployment_id` — and reads the job's progress from Lakebase (the timeline on the app's board).

## The job: four steps in a straight line

```
prepare  →  register  →  validate  →  deploy          (on_failure runs only if a step failed)
```

Each step is one notebook in `src/notebooks/`, and each writes its own **stage** on the app's
timeline:

- **1_prepare** (stage `prepare`) — records the deployment in Lakebase; resolves every path to a
  `/Volumes` path (S3 → its UC external volume); detects each artifact's format and checks it
  matches what the user chose; checks a signature will be available; publishes the **plan**.
- **2_register** (stage `wrapper`) — registers each new variant in Unity Catalog, by format:
  model file → `FileModel` wrapper; MLflow model folder → as-is; code folder → models-from-code.
  Existing versions (A/B champion) are referenced, not registered.
- **3_validate** (stage `validator`) — tests every new variant **in its own environment**
  (`mlflow models predict --env-manager uv`, built from the model's requirements), then scores the
  evaluation dataset there. A real sample that fails stops the deployment here.
- **4_deploy** (stage `deployer`) — remembers the endpoint's current config → creates/updates the
  endpoint → calls every served variant → on a real failure **rolls back** and fails; otherwise
  sets `@champion` and marks the deployment COMPLETE.
- **9_on_failure** (runs only when a step failed) — finds the failed step, writes FAILED + its
  error to Lakebase, then fails the run.

Every notebook starts with `%run ./_setup`, which builds `ctx` (parameters, spec, Lakebase writers).

### What is passed between steps

Steps share data through job **task values** (`ctx.set_value` / `ctx.get_value`):

- `prepare` → **plan**: the variants with resolved paths + detected formats, and the eval dataset path.
- `register` → **variant_versions**: `[{label, version, traffic_percent, format}]`.

The deployment's status row and timeline live in Lakebase (`model_deployer.model_deployments`,
`model_deployer.model_lifecycle_events`), written with `ctx.merge_status(...)` / `ctx.log_event(...)`.

## The code: `src/model_deployer/`

- `context.py` — `Ctx`: job parameters, the deploy spec, the plan, Lakebase writes, and the
  table/grant setup (`pg_init`).
- `artifacts.py` — everything about the model handed in, in four sections: **location**
  (`resolve_path`: UC Volume / S3), **format** (`detect_format`), **contract** (signature +
  examples from the form), **register** (`register_variant`, one function per format).
- `serving.py` — endpoint create/update, tags, AI Gateway, permissions, endpoint test + rollback,
  `@champion`.
- `file_model.py` — the `FileModel` wrapper for single model files. It is its own file because MLflow
  logs it as code (it is never cloudpickled).

## Common changes

- **Add a new artifact location** (e.g. a new storage type): add an `elif` in
  `artifacts.resolve_path()`; add the option to `ARTIFACT_TYPES` in `app/server/server.ts` and the
  form in `app/client/src/pages/DeployModel.tsx`.
- **Add a new artifact format**: add detection in `artifacts.detect_format()`, an `elif` in
  `artifacts.register_variant()` (+ its `_register_<format>()`), and the option in
  `ARTIFACT_FORMATS` (server.ts) + the form.
- **Change package versions**: edit `requirements.txt` (never `%pip install` in a notebook), keep
  `testing/setup_test_artifacts.py` on the same core versions, and redeploy.
- **Change endpoint tags / permissions / AI Gateway**: `serving.py` (`build_tags`,
  `apply_permissions`, `enable_ai_gateway`).

After any change: `pytest tests` (see below), `databricks bundle validate`, then deploy and run a
deployment from `testing/README.md`.

## Debugging a failed deployment

1. On the app's board, expand the row: the red **FAILED** event names the step and the error.
2. In the Jobs UI, open the run (the row's `run_id`) and the failed step's notebook output for the
   full traceback and the `print` logs.
3. Fix the cause, then either deploy again from the app, or use **Repair run** on the job run to
   re-run the failed step and the ones after it (each step is safe to re-run).

## Unit tests

`tests/` covers the pure logic (S3 → volume rewrite, format detection, contract helpers,
requirement patching, tag priority). From `deploy-job/`:

```bash
pip install -r requirements.txt pytest
pytest tests
```

## Platform quirks worth knowing

- **Serverless retries failed tasks automatically**, and a bundle can't turn that off
  (`max_retries: 0` is dropped as a zero value). Every step is written to be safe to re-run; in
  particular `deploy` never treats a config it already applied as the rollback target.
- **Bundle diffs ignore zero / empty values**: adding a parameter whose default is `""` (or a field
  set to `0`) to an existing job is not seen as a change. Give it a non-empty default, or change
  something else in the same deploy.
- **`notebook_params` reach every notebook task**, which is how one `deploy_spec` feeds all steps.
