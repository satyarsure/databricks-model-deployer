# Install in a new (client) workspace

End-to-end runbook to stand up the **Model Deployer** app in a Databricks workspace you can only
reach from a **VDI** (or any machine with network access to that workspace). Everything below runs
on that machine — no dependency on any other environment.

> The app's operational data (deployment status + lifecycle) lives in **Lakebase Postgres**, not a
> SQL warehouse. Unity Catalog still holds the **registered models** and the **artifacts volume**.

> Placeholders used throughout — substitute your real values:
>
> | Placeholder | Meaning | Example |
> |---|---|---|
> | `<WORKSPACE_URL>` | Client workspace URL | `https://adb-123.4.azuredatabricks.net` |
> | `<PROFILE>` | CLI profile for deploys | `client-prod` |
> | `<OAUTH_PROFILE>` | CLI profile for reading app logs | `client-oauth` |
> | `<CATALOG>` / `<SCHEMA>` | UC catalog/schema for **registered models** + the artifacts volume | `rnd_mlops` / `model_deployer` |
> | `<BUDGET_POLICY_ID>` | Serverless budget (usage) policy for chargeback | `d894…506c` |
> | `<APP_NAME>` | Databricks App name (≤26 chars, lowercase/hyphens) | `client-model-deployer` |
> | `<ROOT_PATH>` | Workspace folder the bundles deploy under | `/Workspace/Users/you@client.com/model-deployer` |
> | `<APP_SP_ID>` | The app's service-principal application id (created at app deploy) | `cb3a…6e1c` |
> | `<LB_PROJECT>` | Lakebase project id | `client-model-deployer-lb` |
> | `<LB_BRANCH>` | Lakebase branch resource path | `projects/<LB_PROJECT>/branches/production` |
> | `<LB_DATABASE>` | Lakebase database resource path | `<LB_BRANCH>/databases/databricks-postgres` |
> | `<LB_ENDPOINT>` | Lakebase endpoint resource path | `<LB_BRANCH>/endpoints/primary` |
> | `<PG_HOST>` | Lakebase endpoint host | `ep-….database.<region>.azuredatabricks.net` |
> | `<PG_SCHEMA>` | Postgres schema the job owns for the app tables | `model_deployer` |

---

## 0. What gets created

- **Lakebase project** (Postgres) — the app's operational store. Its `production` branch holds a
  `databricks_postgres` database; the deploy job creates a `<PG_SCHEMA>` schema with two tables:
  `model_deployments` and `model_lifecycle_events` (self-created on the job's first run).
- **Deploy job** (`mlops_deploy_model_job`) — a serverless notebook job (DABs bundle `deploy-job/`).
  Writes status/lifecycle to Lakebase and registers models to UC.
- **Databricks App** `<APP_NAME>` — the React/AppKit UI + Express server (DABs bundle `app/`). Reads
  Lakebase via its `postgres` app resource.
- **A UC catalog/schema + `artifacts` volume** (`<CATALOG>.<SCHEMA>.artifacts`) — where models are
  registered and uploaded/test artifacts live. **No app Delta tables** (they're in Lakebase now).
- Serving endpoints + registered UC models are created per deployment, at runtime, by end users.

---

## Environments (dev / qa / prod)

To deploy the **same code** to several environments with **different values** per environment
(catalog, schema, budget policy, app name, root path, job id, Lakebase coordinates), use a DABs
**target** per environment. The target *names* live in the committed `databricks.yml`; the
per-environment *values* stay in the **gitignored** `values.local.yml`, so nothing is in git.

**Steps 3–7 below are run once per environment**, selecting the target with `-t <env>` and its
profile with `--profile <env>`. If you only need one environment, use the default `dev` target.

**1. Declare the targets once** in each bundle's `databricks.yml` (both `deploy-job/` and `app/`):

```yaml
targets:
  dev:  { mode: development, default: true }   # [dev you] name prefix, schedules paused — scratch env
  qa:   { mode: production }                    # clean names, prod guardrails
  prod: { mode: production }
```

**2. Put per-environment values** in the gitignored `values.local.yml` (one block per target). Deploy job:

```yaml
# deploy-job/values.local.yml
targets:
  dev:
    workspace: { root_path: /Workspace/Users/you@client.com/model-deployer-dev/deploy-job }
    variables:
      catalog: rnd_dev
      schema: model_deployer
      budget_policy_id: <dev-policy>
      cost_center: cc
      team: mlops
      pg_host: <dev-endpoint>.database.<region>.azuredatabricks.net
      pg_database: databricks_postgres
      pg_endpoint: projects/<dev-lb-project>/branches/production/endpoints/primary
      pg_schema: model_deployer
      app_sp: <dev-app-sp-client-id>     # fill after the app is deployed (step 6c)
  # qa: / prod: mirror this with their own values
```
The app bundle's `values.local.yml` mirrors this with `app_name`, `job_id`, `lakebase_branch`, and
`lakebase_database` per target (each env's `job_id` comes from that env's deploy-job deploy — step 5).

**3. One CLI profile per environment** (each is usually a different workspace = different host):

```bash
databricks auth login --host <DEV_URL>  --profile dev
databricks auth login --host <PROD_URL> --profile prod
```

**4. Deploy by selecting target + profile** (applies to every `bundle`/`apps` command in steps 5–6):

```bash
cd deploy-job && databricks bundle deploy -t dev --profile dev
```

**Promotion** = the *same zip/commit* deployed to the next target — only the `values.local.yml`
block and the `--profile` change. Store each environment's real values in your secrets tooling.

> Still deploy from a **non-git copy** (or `rm -rf .git`) in every environment — the git-provenance
> rule in step 1 applies per deploy, regardless of target.

---

## 1. Prerequisites (on the VDI)

1. **Databricks CLI** ≥ v0.294 (the `databricks postgres` command group requires it; validated on
   v1.15.x). Check / install:
   ```bash
   databricks --version
   # macOS/Linux: curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh
   # Windows:     winget install Databricks.DatabricksCLI
   ```
2. **The source code on the VDI.** Get it onto the VDI and unpack it into a working directory, e.g.
   `model-deployer/`. Two supported ways (see the **git provenance** box below):
   ```bash
   # A) delivered as an archive (recommended for a clean handover — no git metadata)
   unzip model-deployer.zip -d model-deployer && cd model-deployer

   # B) kept in your own internal Git
   git clone <YOUR_INTERNAL_GIT_REMOTE_URL> model-deployer && cd model-deployer
   ```

   > **⚠️ Git provenance — how to avoid recording a source-repo URL.**
   > `databricks bundle deploy` auto-detects the git repository of the directory it runs in and
   > stamps its **origin URL, branch, and commit** onto the deployed job/app (shown in the Jobs UI
   > as *"Bundle repository URL"*). There is **no** bundle setting that reliably blanks this. Control
   > it by choosing where you deploy from:
   > - **Deploy from a non-git copy (A)** → the unpacked archive has no `.git`, so **nothing** is
   >   recorded. This is the clean handover.
   > - **Deploy from your own Git (B)** → only **your** origin/branch/commit is recorded, never the vendor's.
   > - **Do NOT** deploy from a checkout that still points at the vendor's remote. If you received a
   >   `git clone` of the vendor repo, strip its history first: `rm -rf .git`.
3. **No Node.js/npm needed locally** — the Databricks Apps runtime runs `npm install` + build on its
   own compute. (Local build is intentionally not required.)
4. **`psql`** is only needed if you want to inspect Lakebase directly (`databricks psql` shells out to
   it) — optional. The deploy job talks to Postgres via `psycopg` on serverless compute.
5. Network access from the VDI to `<WORKSPACE_URL>` (443).

---

## 2. Access & permissions required

**The person installing** needs, in the client workspace:

- **Workspace access** with permission to deploy bundles (write to `<ROOT_PATH>` workspace files),
  create **Lakeflow Jobs**, and create **Databricks Apps**.
- **Lakebase**: rights to create a Postgres project (`databricks postgres create-project`), or the
  branch/database/endpoint resource paths of an existing one. The project **owner** can create
  schemas and grant roles (needed so the deploy job's identity can create `<PG_SCHEMA>`).
- **Unity Catalog**: `USE CATALOG` + `CREATE SCHEMA` on `<CATALOG>` (or an existing `<SCHEMA>`), and
  `CREATE VOLUME` on `<SCHEMA>` (for the artifacts volume + registered models).
- **Serverless budget policy**: rights to create one (Settings → Serverless usage policies), or an
  existing policy id. *Budget policies usually can't be listed via API — get the id from the UI.*

**The app's service principal** (created automatically when the app is deployed):

- Gets a **Lakebase Postgres role** automatically from the app's `postgres` resource
  (`CAN_CONNECT_AND_CREATE`). The deploy job then **grants it `SELECT, INSERT`** on `<PG_SCHEMA>` in
  its `_pg_init` (using the `app_sp` variable) — **no manual UC/DB grant is needed for the app data**.
- **Job `CAN_MANAGE_RUN`** is wired automatically as an app *resource* in `app/databricks.yml`.
- It needs **no Unity Catalog grants** — the app reads Lakebase, not UC.

**The deploy job's run identity** (the deploying user, or a job-owner SP) needs:
- `CREATE MODEL` / `USE` on `<CATALOG>.<SCHEMA>`, permission to create **serving endpoints**, read
  access to wherever artifacts live (the UC volume and/or S3), and use of the budget policy.
- It also applies the deploy form's optional **endpoint permissions** (and grants the submitting
  user `CAN_MANAGE`); as the endpoint's creator it already has `CAN_MANAGE`, so no extra grant is
  needed (a permission-set failure is non-fatal and never fails the deployment).
- **Lakebase access**: it must be able to connect to the database and create/own `<PG_SCHEMA>`. The
  Lakebase project **owner** has this; a job SP needs to be added as a Postgres role with create
  rights (see the databricks-lakebase docs). The job authenticates with an OAuth DB credential for
  its own identity — no static secret.

---

## 3. Authenticate the CLI to the client workspace

Create an OAuth profile (apps-log reading requires OAuth; a PAT can't read app logs):

```bash
databricks auth login --host <WORKSPACE_URL> --profile <OAUTH_PROFILE>
databricks current-user me --profile <OAUTH_PROFILE>
```

You can use the single OAuth profile for everything (set `<PROFILE>` = `<OAUTH_PROFILE>`), or keep a
separate PAT profile for deploys. **Never rely on a default profile — always pass `--profile`.**

---

## 4. Provision Lakebase + Unity Catalog + budget policy

**4a. Lakebase project** (the app's data store). Create one (or reuse an existing project):

```bash
databricks postgres create-project <LB_PROJECT> \
  --json '{"spec": {"display_name": "<LB_PROJECT>"}}' --profile <PROFILE>

# Discover the resource paths + endpoint host you'll need:
databricks postgres list-branches   projects/<LB_PROJECT> --profile <PROFILE>          # -> <LB_BRANCH> (production)
databricks postgres list-databases  <LB_BRANCH> --profile <PROFILE>                    # -> <LB_DATABASE> (databricks_postgres)
databricks postgres list-endpoints  <LB_BRANCH> --profile <PROFILE>                    # -> <LB_ENDPOINT> (primary) + status.hosts.host -> <PG_HOST>
```

The deploy job **self-creates** the `<PG_SCHEMA>` schema and the two tables on its first run — no
manual Postgres DDL is required.

**4b. Unity Catalog** (registered models + artifacts volume). Run once:

```sql
CREATE CATALOG IF NOT EXISTS <CATALOG>;               -- skip if using an existing catalog
CREATE SCHEMA  IF NOT EXISTS <CATALOG>.<SCHEMA>;
CREATE VOLUME  IF NOT EXISTS <CATALOG>.<SCHEMA>.artifacts;
```

**4c. Budget policy** — create in the UI (Settings → Serverless usage policies) and copy `<BUDGET_POLICY_ID>`.

---

## 5. Configure & deploy the deploy job

```bash
cp deploy-job/values.local.example.yml deploy-job/values.local.yml
```
Edit `deploy-job/values.local.yml` (see the Environments section for the full multi-target shape):
```yaml
targets:
  dev:
    workspace:
      root_path: <ROOT_PATH>/deploy-job
    variables:
      catalog: <CATALOG>
      schema: <SCHEMA>
      budget_policy_id: <BUDGET_POLICY_ID>
      cost_center: <your_cost_center>
      team: <your_team>
      pg_host: <PG_HOST>
      pg_database: databricks_postgres
      pg_endpoint: <LB_ENDPOINT>
      pg_schema: <PG_SCHEMA>
      app_sp: ""            # leave blank for now; fill with <APP_SP_ID> after step 6c, then redeploy
```
Deploy and capture the job id:
```bash
cd deploy-job
databricks bundle validate -t dev --profile <PROFILE>
databricks bundle deploy   -t dev --profile <PROFILE>
databricks jobs list --profile <PROFILE> | grep mlops_deploy_model_job   # -> <JOB_ID>
cd ..
```

> The job's serverless environment is pinned (`resources/deploy_model.job.yml` → `environment_version`
> + `requirements.txt`, which includes `psycopg`). If the client's serverless doesn't support that
> environment version, bump it there and redeploy. Don't add `%pip install` to the notebook — change
> `deploy-job/requirements.txt`.

---

## 6. Configure & deploy the app

### 6a. Configure the app bundle
```bash
cp app/values.local.example.yml app/values.local.yml
```
```yaml
targets:
  dev:
    workspace:
      root_path: <ROOT_PATH>/app
    variables:
      app_name: <APP_NAME>
      job_id: <JOB_ID>                 # from step 5
      lakebase_branch: <LB_BRANCH>
      lakebase_database: <LB_DATABASE>
```
(No SQL warehouse and no type-generation sample tables — the app queries Lakebase via Express routes,
not `config/queries`.)

### 6b. Create the app (uploads source + creates the app and its service principal + Postgres role)
```bash
cd app
databricks bundle validate -t dev --profile <PROFILE>
databricks bundle deploy   -t dev --profile <PROFILE>
databricks apps get <APP_NAME> --profile <PROFILE> -o json   # note service_principal_client_id -> <APP_SP_ID>
```
The `postgres` resource provisions the app SP as a Postgres role (`CAN_CONNECT_AND_CREATE`).

### 6c. Wire the app SP into the deploy job (so the job grants it read/write on Lakebase)
Put `<APP_SP_ID>` into `deploy-job/values.local.yml` (`app_sp:`) and redeploy the job:
```bash
cd ../deploy-job && databricks bundle deploy -t dev --profile <PROFILE> && cd ..
```
The job's `_pg_init` now grants the app SP `SELECT, INSERT` on `<PG_SCHEMA>` **on its next run**. The
schema + tables and the grant are created/applied by the **first deployment** (the app shows an empty
board until then, and the app server's instant-submit record works from that point on).

> Bootstrapping note: on the very first submit — before any job run — the schema doesn't exist yet, so
> the app server's initial-record write is skipped (best-effort) and the optimistic row bridges the
> gap; the job creates the schema on cold-start and writes the row. To make the first submit instant,
> trigger one deploy first (e.g. TC1 in `testing/README.md`), which creates the schema + grant.

### 6d. Build & start the app on Apps compute
```bash
cd app
databricks apps deploy <APP_NAME> \
  --source-code-path <ROOT_PATH>/app/files \
  --profile <PROFILE>
cd ..
```
This runs `npm install` + build on Databricks compute and starts the app. Repeat 6b (`bundle deploy`)
+ 6d (`apps deploy`) whenever you change app source.

### 6e. (Optional) Automate the UC schema + artifacts volume via the `governance/` bundle
The app's **data** access (Lakebase) is granted automatically by the deploy job (§6c) — no bundle
needed for it. The optional **`governance/`** bundle only manages the **UC** side declaratively:
it creates the `<CATALOG>.<SCHEMA>` schema + `artifacts` volume (an alternative to the §4b SQL) and
grants a principal (a UC **group** is recommended, or the deploy-job identity) `USE_SCHEMA` /
`READ_VOLUME` so it can register models + read artifacts.

```bash
cp governance/values.local.example.yml governance/values.local.yml   # set catalog, schema, app_grantee
cd governance && databricks bundle deploy -t dev --profile <PROFILE> && cd ..
```
> Deploy `governance/` **only** where you want DABs to own the UC schema/volume — in one where they
> already exist unmanaged, a deploy would conflict; keep the manual §4b SQL there instead. Its
> variables have **no defaults** (a defaults-only deploy fails fast rather than targeting a shared
> schema). The managed schema/volume set `lifecycle: { prevent_destroy: true }` so `bundle destroy`
> can't drop them (verify `prevent_destroy` on your CLI; treat "don't `bundle destroy` prod" as the
> real safeguard).

---

## 7. Verify

```bash
databricks apps get  <APP_NAME> --profile <PROFILE> -o json   # active_deployment.status.state == SUCCEEDED; note `url`
databricks apps logs <APP_NAME> --profile <OAUTH_PROFILE>     # build/runtime logs (OAuth only)
```
Open the app `url`. Then run a smoke test end to end using **`testing/README.md`** (TC1): build the
fixtures with `testing/setup_test_artifacts.py` (pass `<CATALOG>`/`<SCHEMA>`/`artifacts`), deploy the
house-price model, and confirm the row reaches **Complete** with a live lifecycle timeline and the
serving endpoint becomes READY. (Inspect Lakebase directly if needed:
`databricks psql --project <LB_PROJECT> --profile <PROFILE> -- -c "SELECT * FROM <PG_SCHEMA>.model_deployments;"`.)

---

## 8. Troubleshooting (known gotchas)

| Symptom | Cause / fix |
|---|---|
| `databricks apps logs` fails with an OAuth error | Logs need OAuth — use `<OAUTH_PROFILE>` (a PAT can't read them). |
| App error installing packages on deploy | Don't add `@databricks/lakebase` (or pin nonexistent versions) to `app/package.json` — the `lakebase()` plugin ships inside `@databricks/appkit`. |
| App board is empty / `permission denied for schema` in app logs | The deploy job hasn't run yet (schema/tables not created) **or** `app_sp` wasn't set so the job didn't grant the app SP. Set `app_sp` (§6c), redeploy the job, and run one deployment. |
| Deploy job fails immediately at the wrapper stage with a Postgres error | The job's run identity can't connect to / create in Lakebase. Ensure `pg_*` values are correct and the identity has Lakebase access (project owner, or a Postgres role with create). |
| App shows "crashed/exited" right after deploy | Don't build in the start command — `app.yaml` `command` must be start-only. The build runs during `apps deploy`. |
| Deploy job "Bad Request" on trigger | The deploy job must stay a **single** notebook task with `base_parameters` (the AppKit jobs plugin sends `notebook_params`, which a multi-task job rejects). Keep it as shipped. |
| Endpoint description / budget policy not applied on update | Endpoint **tags are re-synced** on update, but `budget_policy_id` + `description` are **create-time only** on serving endpoints — to change them, delete + recreate the endpoint. |
| Serving container fails to load the model | The served model's `pip_requirements` are pinned to wrap-time versions; a **user artifact** must be compatible with the pinned `scikit-learn`/framework in `deploy-job/requirements.txt`. |
| SDK errors about `budget_policy_id` / workload-type enum in the job | `deploy-job/requirements.txt` pins `databricks-sdk`; don't downgrade. |
| Deploy row never appears / stale after reload | The app polls Lakebase every few seconds while something is in progress; confirm the Lakebase endpoint is reachable and the app SP has `SELECT` on `<PG_SCHEMA>`. |

---

## 9. Uninstall / teardown

```bash
# Delete serving endpoints created by end users first (per endpoint):
databricks serving-endpoints delete <endpoint-name> --profile <PROFILE>

# Destroy the bundles (removes the app and the job). Pass the env target you deployed (-t dev|qa|prod):
cd app        && databricks bundle destroy -t dev --profile <PROFILE> && cd ..
cd deploy-job && databricks bundle destroy -t dev --profile <PROFILE> && cd ..
# If you deployed the optional governance bundle, destroy it too (prevent_destroy guards the UC
# schema/volume):
# cd governance && databricks bundle destroy -t dev --profile <PROFILE> && cd ..

# Optionally drop the Lakebase data (irreversible) — via psql:
# databricks psql --project <LB_PROJECT> --profile <PROFILE> -- -c "DROP SCHEMA IF EXISTS <PG_SCHEMA> CASCADE;"
# ...or delete the whole Lakebase project (removes ALL its data):
# databricks postgres delete-project projects/<LB_PROJECT> --profile <PROFILE>

# Optionally drop the UC artifacts volume (irreversible):
# DROP VOLUME <CATALOG>.<SCHEMA>.artifacts;
```
