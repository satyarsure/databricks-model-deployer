# databricks-model-deployer

A Databricks App (React/AppKit) that lets users hand in an external model artifact from **S3** or a
**Unity Catalog Volume** and publish it to **Databricks Model Serving** — reusing the MLflow
deployment pattern from Genesis Workbench.

A parameterized workflow job wraps the artifact as an MLflow **pyfunc**, registers it to **Unity
Catalog**, validates it, and creates/updates a **serving endpoint** (A/B traffic split, CPU/GPU
sizing, scale-to-zero, inference tables, and a serverless **budget policy** for chargeback).
Deployment status and full **model lifecycle history** are tracked in **Lakebase Postgres** (for
sub-second, real-time reads on the live status board), with the active version marked by a UC
`@champion` alias.

An A/B variant can be a **new artifact** (wrapped and registered as a new version) or an
**already-registered version** of the same model referenced as-is — a champion-vs-challenger test
that serves the current model against a new candidate on the same endpoint.

## Architecture

```
React/AppKit app
 ├─ Deployed Models tab  → reads Lakebase Postgres via Express routes (/api/deployments,
 │                          /api/lifecycle, /api/model-versions) — search + pagination, status,
 │                          "Open" links, a live lifecycle timeline per row, resizable columns;
 │                          click a model name to deploy a new version, or "A/B test".
 └─ Deploy Model tab     → POST /api/deploy → (1) writes an initial "submitted" record to Lakebase
                            so a real row shows instantly, then (2) triggers the deploy job.
                                                   │
Deploy job (DABs, serverless) — one notebook, three stages:
   Wrapper    → load artifact(s), wrap as MLflow pyfunc, build signature, register each new-artifact
                variant to UC (a variant may instead reference an existing registered version)
   Validator  → load the registered pyfunc, smoke-test predict, optional mlflow.evaluate vs an eval dataset
   Deployer   → create/update the serving endpoint (traffic split, compute, scale-to-zero, tags,
                budget policy, inference tables, access permissions); set the UC @champion alias
   (every stage writes status to Lakebase model_deployments and appends to model_lifecycle_events)

Lakebase Postgres (schema `model_deployer`) — the app's operational store:
   model_deployments      → one upserted row per deployment (keyed on deployment_id)
   model_lifecycle_events → append-only stage/status transitions
   The deploy job OWNS the schema (self-creates the tables via psycopg) and GRANTs the app's
   service principal SELECT + INSERT. The app reads them; the app server writes the initial record.
```

## Repository layout

| Path | Description |
|------|-------------|
| `app/` | The React/AppKit app (frontend + Express server). Reads Lakebase via Express routes (`server/server.ts`) — no `config/queries` / analytics warehouse. |
| `app/client/src/lib/useApiQuery.ts` | Small client hook that fetches the app's JSON routes (replaces `useAnalyticsQuery`). |
| `deploy-job/` | The DABs bundle for the deploy workflow job (`src/notebooks/deploy_model.py`). Writes status/lifecycle to Lakebase Postgres. |
| `deploy-job/requirements.txt` | Pinned dependency set for the job's serverless environment (includes `psycopg`). |
| `governance/` | Optional DABs bundle that declaratively creates the **UC schema + `artifacts` volume** (where models are registered and artifacts live). The app's data lives in Lakebase, granted automatically by the job — see [INSTALL.md](INSTALL.md) §6f. |
| `testing/` | Manual-testing fixtures (`setup_test_artifacts.py`) and guide (`README.md`). |
| `Images/` | UI mockups. |

## Reproducible dependencies (version locking)

Package versions are pinned in two places so a serverless base-image change can't silently break
a run or drift predictions:

- **Deploy job** — the notebook does **no** `%pip install`. Instead the job declares a pinned
  serverless environment (`resources/deploy_model.job.yml` → `environments[].spec`) with
  `environment_version` (pins the Python runtime) and `-r requirements.txt` (pins every package,
  including `psycopg` for the Postgres writes).
- **Served model** — the notebook pins the logged model's `pip_requirements` to the **exact
  versions actually in use at wrap time** (`mlflow=={version}`, `scikit-learn==…`, etc.). Model
  Serving rebuilds the container from these requirements (including on scale-to-zero cold starts),
  so the serving environment always matches the versions the artifact was loaded/pickled with.

Keep `testing/setup_test_artifacts.py` pinned to the same core versions as `requirements.txt` so
fixture pickles load without a version-mismatch warning. Note: a **user-supplied** artifact must be
compatible with the pinned `scikit-learn` (or its framework) — pin your training environment to
match, or update `requirements.txt` to the version your artifact was trained with.

## Data store

**Lakebase Postgres** (schema `model_deployer` in a Lakebase project's database) is the app's
operational store — chosen over a SQL warehouse because the Deployed Models board is a live,
frequently-updated status view that needs point reads at OLTP latency:

- **`model_deployments`** — one row per deployment (name, UC name, version, status/stage, endpoint,
  compute, tags, schemas, budget policy, endpoint permissions, timestamps). Upserted by `deployment_id`.
- **`model_lifecycle_events`** — append-only audit trail of every stage/status transition.

The **deploy job owns the schema**: on each run it self-creates the schema + tables (`CREATE …
IF NOT EXISTS`, via `psycopg`) and grants the app's service principal `SELECT, INSERT`. It connects
using a short-lived OAuth **database credential** for its own identity (`POST
/api/2.0/postgres/credentials`) — no static secret. The **app server** writes the initial
"submitted" record on `/api/deploy` (so a real row appears during the job's serverless cold-start),
then the job upserts the same row as it runs.

**Unity Catalog** (`<catalog>.<schema>`) still holds the **registered models** and the **`artifacts`
volume** (uploaded/test artifacts). The catalog/schema/volume are provisioned up front; the two app
tables are **not** in UC anymore — they live in Lakebase.

## Configuration (no environment-specific values are committed)

All bundles read a **gitignored `values.local.yml`** (merged via each bundle's `include`). Copy the
examples and fill them in:

```bash
cp deploy-job/values.local.example.yml deploy-job/values.local.yml
cp app/values.local.example.yml        app/values.local.yml
# governance/ is optional (see INSTALL §6f):
# cp governance/values.local.example.yml governance/values.local.yml
```

**Everything deployment-specific is a bundle variable** — set entirely under `variables:` in
`values.local.yml`, including the deploy path, MLflow experiment, chargeback tags, and serverless
policy. The Lakebase resource paths are composed from the project **name**, so you set it once.

- **deploy-job** — `root_path` (deploy path), `catalog` / `schema` (UC, for models + artifacts),
  `experiment` (deploy-time MLflow default), `budget_policy_id` (one serverless usage policy for the
  job, the app, **and** every serving endpoint), the chargeback tags `application` / `cost_center` /
  `team` (an `environment` tag is added automatically from the target), `lakebase_project` (the
  branch/endpoint paths are composed from it), `pg_host` (the endpoint host — not derivable), and
  `app_sp` (the app's service-principal client id it grants `SELECT, INSERT`). `pg_database`,
  `pg_schema`, `lakebase_branch_name`, `lakebase_endpoint_name` have sensible defaults.
- **app** — `root_path`, `app_name`, `job_id` (the deploy job), `budget_policy_id` (same policy —
  Databricks Apps take no custom tags, so this is their cost-attribution handle), and
  `lakebase_project` (branch/database paths composed from it). The app declares a `postgres` resource
  (not a warehouse); the Apps platform injects `PGHOST`/`PGDATABASE`/`PGUSER`/… and `LAKEBASE_ENDPOINT`.

The **Experiment** and **Serverless usage policy** are also Deploy-form fields, but **optional
overrides** — left blank, a deployment uses the environment's configured `experiment` /
`budget_policy_id`.

## Deploy

> **Installing in a new / client workspace?** See **[INSTALL.md](INSTALL.md)** for the full runbook —
> prerequisites, required access/permissions, Lakebase + UC provisioning, the deploy order (and its
> bootstrapping steps), verification, and troubleshooting. The steps below are the quick reference
> for a workspace that's already provisioned. For multiple environments (dev/qa/prod), see the
> **Environments** section in INSTALL.

Uses your Databricks CLI profile for the workspace host (pass `--profile <PROFILE>`).

```bash
# 1) Deploy job (creates/updates the deploy job)
cd deploy-job
databricks bundle deploy -t dev --profile <PROFILE>

# 2) App — Databricks Apps compute runs `npm install` + build at startup
cd ../app
databricks bundle deploy -t dev --profile <PROFILE>
databricks apps deploy <app-name> \
  --source-code-path <workspace-root_path>/app/files \
  --profile <PROFILE>
```

> Note: local `npm install`/build may be blocked by network policy on some machines; the Databricks
> Apps runtime installs dependencies and builds the app on its own compute. App logs require an OAuth
> profile: `databricks apps logs <app-name> -p <oauth-profile>`.

> **Git provenance:** `databricks bundle deploy` auto-detects the git repo of the directory it runs
> in and stamps its **origin URL / branch / commit** onto the deployed job & app (shown in the Jobs UI
> as *"Bundle repository URL"*) — there is no bundle setting that reliably blanks this. To avoid
> recording a source-repo URL in a client workspace, **deploy from a non-git copy** (unpack the
> delivered archive, or `rm -rf .git` in the checkout) so nothing is recorded; deploying from your own
> internal Git records only your origin, never the vendor's. See [INSTALL.md](INSTALL.md) → *Git
> provenance*.

## Chargeback

One serverless usage policy (`budget_policy_id`) and one tag set (`application` / `cost_center` /
`team` / `environment`) are configured once per environment and applied everywhere Model Deployer
spends serverless compute:

- **Serving endpoint** — gets `budget_policy_id` (the form's *Serverless usage policy* if set, else
  the environment default) at create time, plus the standard tag set **and** any per-deployment
  *Tags* from the form (form tags override the standard set on key collisions). **Tags are re-synced
  on every new-version/A/B update** (via the tags API); `budget_policy_id` and `description` are
  **create-time only** (no serving API updates them post-create).
- **Deploy job** — carries the same `budget_policy_id` + tag set (via `deploy-job` bundle variables)
  so the deployment compute is attributed.
- **App** — carries the same `budget_policy_id` (Databricks Apps don't support custom tags via DABs,
  so the policy is the app's cost-attribution handle).

## Tags / metadata

The form's **Tags** field is a free-form JSON object; each key/value is applied to the serving
endpoint (and re-synced on updates) and persisted in `model_deployments.tags`. Beyond chargeback
(`cost_center`, `team`), this is the extensible place for **governance/ownership metadata** — e.g.
use-case / APMS ID, business / technical / support owner, environment, GxP classification,
deployment mode — without any schema change, so new fields can be added as more workloads onboard.

## Endpoint permissions

The form's optional **Endpoint permissions** field is a JSON object that grants principals access to
the serving endpoint, grouped by level:

```json
{
  "can_manage": ["someone@company.com"],
  "can_query": ["a-databricks-group"],
  "can_view": ["9b1a2c3d-4e5f-6789-abcd-ef0123456789"]
}
```

- Levels are `can_manage` / `can_query` / `can_view`. Each principal is classified by shape: an
  **email** → user, a **36-char UUID** → service principal, anything else → **group**. List all
  principals for a level in **one array** (duplicate keys are rejected by the form).
- The **user who submits the deploy** (captured from the app's signed-in identity — which may differ
  from the deploy job's run-as identity) is granted **`CAN_MANAGE` by default**, *unless* they are
  listed explicitly, in which case the level you gave is honored.
- Applied by the deploy job once the endpoint exists, as a **PATCH (merge)** — the endpoint
  owner/creator and any pre-existing ACLs are preserved. Non-fatal (a permission error is logged as a
  lifecycle note and never fails the deployment) and persisted in `model_deployments.permissions_json`.

## Credits

Model deployment logic adapted from the
[Genesis Workbench](https://github.com/databricks-industry-solutions/genesis-workbench) solution.
