# databricks-model-deployer

A Databricks App (React/AppKit) that lets users hand in an external model artifact from **S3** or a
**Unity Catalog Volume** and publish it to **Databricks Model Serving** — reusing the MLflow
deployment pattern from Genesis Workbench.

A parameterized workflow job wraps the artifact as an MLflow **pyfunc**, registers it to **Unity
Catalog**, validates it, and creates/updates a **serving endpoint** (A/B traffic split, CPU/GPU
sizing, scale-to-zero, inference tables, and a serverless **budget policy** for chargeback).
Deployment status and full **model lifecycle history** are tracked in Delta tables, with the active
version marked by a UC `@champion` alias.

An A/B variant can be a **new artifact** (wrapped and registered as a new version) or an
**already-registered version** of the same model referenced as-is — a champion-vs-challenger test
that serves the current model against a new candidate on the same endpoint.

## Architecture

```
React/AppKit app
 ├─ Deployed Models tab  → reads the model_deployments Delta table (search + pagination, status,
 │                          "Open" links, a live lifecycle timeline per row; click a model name to
 │                          deploy a new version, or "A/B test" to pit it against a new candidate)
 └─ Deploy Model tab     → POST /api/deploy → triggers the deploy job
                                                   │
Deploy job (DABs, serverless) — one notebook, three stages:
   Wrapper    → load artifact(s), wrap as MLflow pyfunc, build signature, register each new-artifact
                variant to UC (a variant may instead reference an existing registered version)
   Validator  → load the registered pyfunc, smoke-test predict, mlflow.evaluate vs the (required) eval dataset
   Deployer   → create/update the serving endpoint (traffic split, compute, scale-to-zero, tags,
                budget policy, inference tables); set the UC @champion alias
   (every stage writes status to model_deployments and appends to model_lifecycle_events)
```

## Repository layout

| Path | Description |
|------|-------------|
| `app/` | The React/AppKit app (frontend + Express server). Deployed to Databricks Apps. |
| `deploy-job/` | The DABs bundle for the deploy workflow job (`src/notebooks/deploy_model.py`). |
| `deploy-job/requirements.txt` | Pinned dependency set for the job's serverless environment. |
| `testing/` | Manual-testing fixtures (`setup_test_artifacts.py`) and guide (`README.md`). |
| `Images/` | UI mockups. |

## Reproducible dependencies (version locking)

Package versions are pinned in two places so a serverless base-image change can't silently break
a run or drift predictions:

- **Deploy job** — the notebook does **no** `%pip install`. Instead the job declares a pinned
  serverless environment (`resources/deploy_model.job.yml` → `environments[].spec`) with
  `environment_version` (pins the Python runtime) and `-r requirements.txt` (pins every package).
  Change versions by editing `deploy-job/requirements.txt` and redeploying — never by editing the
  notebook.
- **Served model** — the notebook pins the logged model's `pip_requirements` to the **exact
  versions actually in use at wrap time** (`mlflow=={version}`, `scikit-learn==…`, etc.). Model
  Serving rebuilds the container from these requirements (including on scale-to-zero cold starts),
  so the serving environment always matches the versions the artifact was loaded/pickled with.

Keep `testing/setup_test_artifacts.py` pinned to the same core versions as `requirements.txt` so
fixture pickles load without a version-mismatch warning. Note: a **user-supplied** artifact must be
compatible with the pinned `scikit-learn` (or its framework) — pin your training environment to
match, or update `requirements.txt` to the version your artifact was trained with.

## Configuration (no environment-specific values are committed)

Both bundles read a **gitignored `values.local.yml`** (merged via each bundle's `include`) for the
real workspace path, catalog/schema, app name, and IDs. To set up a deployment, copy the example and
fill it in:

```bash
cp deploy-job/values.local.example.yml deploy-job/values.local.yml
cp app/values.local.example.yml       app/values.local.yml
# then edit both with your workspace root_path, catalog, schema, app name, and IDs
```

The app resolves which `catalog.schema.model_deployments` table to read **from the deploy job's
notebook parameters at runtime** (via `/api/config`), so the app source has nothing environment-specific.

Type generation (build time) resolves the query's columns from the generic sample table named in
`app/config/queries/deployments.sql` (`main.default.model_deployments` by default). Create an empty
table with the `model_deployments` schema at that name, or change the sample value to your own —
the runtime query still binds the real table. (The deploy-job creates the real `model_deployments`
table on first run.)

## Deploy

> **Installing in a new / client workspace?** See **[INSTALL.md](INSTALL.md)** for the full runbook —
> prerequisites, required access/permissions, UC provisioning, the deploy order (and its
> bootstrapping steps), verification, and troubleshooting. The steps below are the quick reference
> for a workspace that's already provisioned.

Uses your Databricks CLI profile for the workspace host (pass `--profile <PROFILE>`).

```bash
# 1) Deploy job (creates/updates the deploy job)
cd deploy-job
databricks bundle deploy --target dev --profile <PROFILE>

# 2) App — Databricks Apps compute runs `npm install` + build at startup
cd ../app
databricks bundle deploy -t default --profile <PROFILE>
databricks apps deploy <app-name> \
  --source-code-path <workspace-root_path>/app/files \
  --profile <PROFILE>
```

> Note: local `npm install`/build may be blocked by network policy on some machines; the Databricks
> Apps runtime installs dependencies and builds the app on its own compute. App logs require an OAuth
> profile: `databricks apps logs <app-name> -p <oauth-profile>`.

## Data model (Unity Catalog: `<catalog>.<schema>`)

- **`model_deployments`** — one row per deployment (name, UC name, version, status/stage, endpoint,
  compute, tags, schemas, budget policy, timestamps). Source of truth for the Deployed Models list.
- **`model_lifecycle_events`** — append-only audit trail of every stage/status transition.
- **`artifacts`** volume — holds uploaded/test model artifacts.

The `catalog`/`schema` and the `artifacts` volume must be provisioned up front (UC admin/location);
the deploy job **self-creates** the two Delta tables on its first run (`CREATE TABLE IF NOT EXISTS`),
so no manual table DDL is required. See [INSTALL.md](INSTALL.md).

## Chargeback

- **Serving endpoint** — the form's *Serverless usage policy* (a budget policy ID) becomes the
  endpoint `budget_policy_id`; the *Description* and *Tags* are applied at creation.
- **Deploy job** — carries its own `budget_policy_id` + tags (configurable via `deploy-job` bundle
  variables) so the deployment compute is attributed.

## Tags / metadata

The form's **Tags** field is a free-form JSON object; each key/value is applied to the serving
endpoint and persisted in `model_deployments.tags`. Beyond chargeback (`cost_center`, `team`), this
is the extensible place for **governance/ownership metadata** — e.g. use-case / APMS ID, business /
technical / support owner, environment, GxP classification, deployment mode — without any schema
change, so new fields can be added as more workloads onboard.

## Credits

Model deployment logic adapted from the
[Genesis Workbench](https://github.com/databricks-industry-solutions/genesis-workbench) solution.
