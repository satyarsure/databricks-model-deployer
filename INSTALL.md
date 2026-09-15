# Install in a new (client) workspace

End-to-end runbook to stand up the **Model Deployer** app in a Databricks workspace you can only
reach from a **VDI** (or any machine with network access to that workspace). Everything below runs
on that machine — no dependency on any other environment.

> Placeholders used throughout — substitute your real values:
>
> | Placeholder | Meaning | Example |
> |---|---|---|
> | `<WORKSPACE_URL>` | Client workspace URL | `https://adb-123.4.azuredatabricks.net` |
> | `<PROFILE>` | CLI profile for deploys | `client-prod` |
> | `<OAUTH_PROFILE>` | CLI profile for reading app logs | `client-oauth` |
> | `<CATALOG>` / `<SCHEMA>` | UC catalog/schema for the app's tables + volume | `rnd_mlops` / `model_deployer` |
> | `<WAREHOUSE_ID>` | SQL warehouse the app queries | `1a2b3c4d5e6f7g8h` |
> | `<BUDGET_POLICY_ID>` | Serverless budget (usage) policy for chargeback | `d894…506c` |
> | `<APP_NAME>` | Databricks App name (≤26 chars, lowercase/hyphens) | `client-model-deployer` |
> | `<ROOT_PATH>` | Workspace folder the bundles deploy under | `/Workspace/Users/you@client.com/model-deployer` |
> | `<APP_SP_ID>` | The app's service-principal application id (created at app deploy) | `cb3a…6e1c` |

---

## 0. What gets created

- **Deploy job** (`mlops_deploy_model_job`) — a serverless notebook job (DABs bundle `deploy-job/`).
- **Databricks App** `<APP_NAME>` — the React/AppKit UI + Express server (DABs bundle `app/`).
- **Two Delta tables** in `<CATALOG>.<SCHEMA>`: `model_deployments`, `model_lifecycle_events`
  (the job self-creates these on its first run; you can also pre-create them — see step 4).
- **A UC volume** `<CATALOG>.<SCHEMA>.artifacts` (holds uploaded/test model artifacts).
- Serving endpoints + registered UC models are created per deployment, at runtime, by end users.

---

## Environments (dev / qa / prod)

To deploy the **same code** to several environments with **different values** per environment
(catalog, schema, warehouse, budget policy, app name, root path, job id), use a DABs **target** per
environment. The target *names* live in the committed `databricks.yml`; the per-environment *values*
stay in the **gitignored** `values.local.yml`, so nothing environment-specific is in git.

**Steps 3–7 below are run once per environment**, selecting the target with `-t <env>` and its profile
with `--profile <env>`. If you only need a single environment, ignore this section and use the default
target as-is.

**1. Declare the targets once** in each bundle's `databricks.yml` (both `deploy-job/` and `app/`):

```yaml
targets:
  dev:  { mode: development, default: true }   # [dev you] name prefix, schedules paused — scratch env
  qa:   { mode: production }                    # clean names, prod guardrails
  prod: { mode: production }
```

**2. Put the per-environment values** in the gitignored `values.local.yml` — the existing `*.local.yml`
include merges it — with one block per target. Deploy job:

```yaml
# deploy-job/values.local.yml
targets:
  dev:
    workspace: { root_path: /Workspace/Users/you@client.com/model-deployer-dev/deploy-job }
    variables: { catalog: rnd_dev,  schema: model_deployer, budget_policy_id: <dev-policy>,  cost_center: cc, team: mlops }
  qa:
    workspace: { root_path: /Workspace/Shared/model-deployer-qa/deploy-job }
    variables: { catalog: rnd_qa,   schema: model_deployer, budget_policy_id: <qa-policy>,   cost_center: cc, team: mlops }
  prod:
    workspace: { root_path: /Workspace/Shared/model-deployer-prod/deploy-job }
    variables: { catalog: rnd_prod, schema: model_deployer, budget_policy_id: <prod-policy>, cost_center: cc, team: mlops }
```
The app bundle's `values.local.yml` mirrors this with `app_name`, `sql_warehouse_id`, and `job_id` per
target (each env's `job_id` comes from that env's deploy-job deploy — step 5).

**3. One CLI profile per environment** (each is usually a different workspace = different host):

```bash
databricks auth login --host <DEV_URL>  --profile dev
databricks auth login --host <QA_URL>   --profile qa
databricks auth login --host <PROD_URL> --profile prod
```

**4. Deploy by selecting target + profile** (applies to every `bundle`/`apps` command in steps 5–6):

```bash
cd deploy-job
databricks bundle deploy -t dev  --profile dev
databricks bundle deploy -t qa   --profile qa
databricks bundle deploy -t prod --profile prod
```

**Promotion** = the *same zip/commit* deployed to the next target — only the `values.local.yml` target
block and the `--profile` change. Store each environment's real values in your deployment/secrets
tooling, not in git.

> Still deploy from a **non-git copy** (or `rm -rf .git`) in every environment — the git-provenance
> rule in step 1 applies per deploy, regardless of target.

---

## 1. Prerequisites (on the VDI)

1. **Databricks CLI** ≥ v0.240 (this project was validated on v1.15.x). Check / install:
   ```bash
   databricks --version
   # macOS/Linux: curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh
   # Windows:     winget install Databricks.DatabricksCLI
   ```
2. **The source code on the VDI.** Get it onto the VDI and unpack it into a working directory, e.g.
   `model-deployer/`. Two supported ways (see the box below on **git provenance** — it decides which
   to use):
   ```bash
   # A) delivered as an archive (recommended for a clean handover — no git metadata)
   unzip model-deployer.zip -d model-deployer && cd model-deployer

   # B) kept in your own internal Git
   git clone <YOUR_INTERNAL_GIT_REMOTE_URL> model-deployer && cd model-deployer
   ```

   > **⚠️ Git provenance — how to avoid recording a source-repo URL.**
   > `databricks bundle deploy` auto-detects the git repository of the directory it runs in and
   > stamps its **origin URL, branch, and commit** onto the deployed job/app (shown in the Jobs UI
   > as *"Bundle repository URL"*). There is **no** bundle setting that reliably blanks this — the
   > CLI re-detects at deploy time. So control it by choosing where you deploy from:
   > - **Deploy from a non-git copy (A)** → the unpacked archive has no `.git`, so **nothing** is
   >   recorded (no URL, branch, or commit). This is the clean handover.
   > - **Deploy from your own Git (B)** → only **your** origin/branch/commit is recorded, never the
   >   vendor's — which is fine and expected.
   > - **Do NOT** deploy from a checkout that still points at the vendor's remote. If you received a
   >   `git clone` of the vendor repo, strip its history first:
   >   ```bash
   >   rm -rf .git      # removes vendor origin/branch/commit; deploy now records no git metadata
   >   ```
3. **No Node.js/npm needed locally** — the Databricks Apps runtime runs `npm install` + build on its
   own compute. (Local build is intentionally not required.)
4. Network access from the VDI to `<WORKSPACE_URL>` (443).

---

## 2. Access & permissions required

**The person installing** needs, in the client workspace:

- **Workspace access** with permission to deploy bundles (write to `<ROOT_PATH>` workspace files),
  create **Lakeflow Jobs**, and create **Databricks Apps**.
- **SQL warehouse**: rights to create one, or the id of an existing (serverless recommended) warehouse.
- **Unity Catalog**: `USE CATALOG` + `CREATE SCHEMA` on `<CATALOG>` (or an existing `<SCHEMA>`),
  and `CREATE TABLE` + `CREATE VOLUME` on `<SCHEMA>`. To grant the app's service principal later you
  must be **owner** of (or have `MANAGE` on) the catalog/schema.
- **Serverless budget policy**: rights to create one (Settings → Serverless usage policies), or an
  existing policy id. *Budget policies usually can't be listed via API — get the id from the UI.*

**The app's service principal** (created automatically when the app is deployed) needs — you grant
these in step 6:

- `USE CATALOG` on `<CATALOG>`, `USE SCHEMA` on `<CATALOG>.<SCHEMA>`
- `SELECT` on `<CATALOG>.<SCHEMA>.model_deployments` and `.model_lifecycle_events`
- `READ VOLUME` (and `WRITE VOLUME` if users upload artifacts through UC) on `.artifacts`
- **Warehouse `CAN_USE`** and **job `CAN_MANAGE_RUN`** — these are wired automatically as app
  *resources* in `app/databricks.yml`, so no manual grant is needed for them.

**The deploy job's run identity** (the deploying user, or a job-owner SP) needs `CREATE MODEL` /
`USE` on `<CATALOG>.<SCHEMA>`, permission to create **serving endpoints**, read access to wherever
artifacts live (the UC volume and/or the S3 path), and use of the budget policy.

---

## 3. Authenticate the CLI to the client workspace

Create **two** profiles (apps-log reading requires OAuth; a PAT can't read app logs):

```bash
# OAuth (used for deploys AND for `databricks apps logs`)
databricks auth login --host <WORKSPACE_URL> --profile <OAUTH_PROFILE>

# Verify
databricks current-user me --profile <OAUTH_PROFILE>
```

You can use the single OAuth profile for everything below (set `<PROFILE>` = `<OAUTH_PROFILE>`), or
keep a separate PAT profile for deploys. **Never rely on a default profile — always pass `--profile`.**

---

## 4. Provision Unity Catalog assets + warehouse + budget policy

Run these once in the client workspace (SQL editor, a notebook, or CLI). Adjust the volume's storage
as your governance requires (managed volume shown).

```sql
CREATE CATALOG IF NOT EXISTS <CATALOG>;               -- skip if using an existing catalog
CREATE SCHEMA  IF NOT EXISTS <CATALOG>.<SCHEMA>;
CREATE VOLUME  IF NOT EXISTS <CATALOG>.<SCHEMA>.artifacts;
```

**Delta tables** — the deploy job self-creates them on its first run, so this is optional. Creating
them now lets the app build (step 7) succeed immediately without a prior job run:

```sql
CREATE TABLE IF NOT EXISTS <CATALOG>.<SCHEMA>.model_deployments (
  deployment_id BIGINT, model_name STRING, description STRING,
  uc_catalog STRING, uc_schema STRING, uc_model STRING, uc_full_name STRING,
  model_version STRING, experiment_name STRING, eval_dataset STRING,
  serverless_usage_policy STRING, tags STRING,
  compute_type STRING, gpu_type STRING, compute_size STRING, scale_to_zero BOOLEAN,
  artifacts_json STRING, input_schema_json STRING, output_schema_json STRING,
  endpoint_name STRING, invoke_url STRING, status STRING, stage STRING, error_message STRING,
  deployed_by STRING, deployed_date TIMESTAMP, updated_at TIMESTAMP, run_id STRING
) USING DELTA;

CREATE TABLE IF NOT EXISTS <CATALOG>.<SCHEMA>.model_lifecycle_events (
  event_id BIGINT, deployment_id BIGINT, model_name STRING, uc_full_name STRING,
  model_version STRING, stage STRING, status STRING, message STRING,
  actor STRING, event_time TIMESTAMP
) USING DELTA;
```

**SQL warehouse** — create one (serverless recommended) or note an existing id:
```bash
databricks warehouses list --profile <PROFILE>          # find <WAREHOUSE_ID>
```

**Budget policy** — create in the UI (Settings → Serverless usage policies) and copy `<BUDGET_POLICY_ID>`.

---

## 5. Configure & deploy the deploy job

```bash
cp deploy-job/values.local.example.yml deploy-job/values.local.yml
```
Edit `deploy-job/values.local.yml`:
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
```
Deploy and capture the job id (**multi-env:** add `-t <env>` and use that env's `--profile`; the
`values.local.yml` block for that target supplies the values):
```bash
cd deploy-job
databricks bundle validate            --profile <PROFILE>   # add -t <env> for qa/prod
databricks bundle deploy              --profile <PROFILE>   # add -t <env> for qa/prod
databricks jobs list --profile <PROFILE> | grep mlops_deploy_model_job   # -> <JOB_ID> (per env)
cd ..
```

> The job's serverless environment is pinned (`resources/deploy_model.job.yml` → `environment_version`
> + `requirements.txt`). If the client's serverless doesn't support that environment version, bump it
> there and redeploy. Don't add `%pip install` to the notebook — change `deploy-job/requirements.txt`.

---

## 6. Configure & deploy the app; grant its service principal

### 6a. Point type-generation at real tables
The app build resolves query columns from the **sample** table named in each
`app/config/queries/*.sql` `@param` (default `main.default.model_deployments` /
`model_lifecycle_events`). Pick one:

- **Option A (no repo edits):** create empty tables at `main.default.model_deployments` and
  `main.default.model_lifecycle_events` (same DDL as step 4) and grant the app SP `SELECT` on them.
- **Option B:** edit the sample defaults in `deployments.sql`, `lifecycle.sql`, `model_versions.sql`
  to `<CATALOG>.<SCHEMA>.model_deployments` / `.model_lifecycle_events` (these must exist — step 4).

### 6b. Configure the app bundle
```bash
cp app/values.local.example.yml app/values.local.yml
```
```yaml
targets:
  default:
    workspace:
      root_path: <ROOT_PATH>/app
    variables:
      app_name: <APP_NAME>
      sql_warehouse_id: <WAREHOUSE_ID>
      job_id: <JOB_ID>            # from step 5
```

### 6c. Create the app (uploads source + creates the app and its service principal)
```bash
cd app
databricks bundle validate           --profile <PROFILE>   # add -t <env> for qa/prod
databricks bundle deploy -t default  --profile <PROFILE>   # -t <env> if you defined dev/qa/prod targets
databricks apps get <APP_NAME> --profile <PROFILE> -o json   # note service_principal_* -> <APP_SP_ID>
```

### 6d. Grant the app's service principal (now that it exists)
```sql
GRANT USE CATALOG ON CATALOG <CATALOG> TO `<APP_SP_ID>`;
GRANT USE SCHEMA  ON SCHEMA  <CATALOG>.<SCHEMA> TO `<APP_SP_ID>`;
GRANT SELECT ON TABLE <CATALOG>.<SCHEMA>.model_deployments      TO `<APP_SP_ID>`;
GRANT SELECT ON TABLE <CATALOG>.<SCHEMA>.model_lifecycle_events TO `<APP_SP_ID>`;
GRANT READ VOLUME  ON VOLUME <CATALOG>.<SCHEMA>.artifacts TO `<APP_SP_ID>`;
-- GRANT WRITE VOLUME ON VOLUME <CATALOG>.<SCHEMA>.artifacts TO `<APP_SP_ID>`;  -- if users upload via UC
-- If you used Option A above, also GRANT SELECT on the two main.default sample tables.
```

### 6e. Build & start the app on Apps compute
```bash
databricks apps deploy <APP_NAME> \
  --source-code-path <ROOT_PATH>/app/files \
  --profile <PROFILE>
cd ..
```
This runs `npm install` + type-generation + build on Databricks compute and starts the app. Repeat
6c (`bundle deploy`) + 6e (`apps deploy`) whenever you change app source.

### 6f. (Optional) Automate the UC grants via the bundle instead of step 6d

Yes — most of step 6 can be folded into `bundle deploy`. Some of it already is: the app's service
principal is **created** by the app deploy, and its **warehouse `CAN_USE` + job `CAN_MANAGE_RUN`**
grants are declared as app *resources* in `app/databricks.yml` (applied automatically). The remaining
**UC** grants can also be declared in the bundle so a deploy applies them — with two facts to design
around (both confirmed against the DABs schema):

- **No table-level grant.** DABs has resource types for `Catalog` / `Schema` / `Volume` (with a
  `grants:` block) but **not for tables**. So grant **`SELECT` at the schema level** — it covers
  `model_deployments`, `model_lifecycle_events`, and any future table in the schema.
- **The app SP id isn't a bundle reference.** The `app` resource does not expose the service-principal
  id it mints, so the bundle can't grant that SP in the *same* first deploy. Two ways around it:
  - **Grant to a UC group (recommended).** Grant to a group in the bundle (static), and add each
    environment's app SP to that group once. Fully declarative, one-shot, identical across dev/qa/prod.
  - **Grant to the SP by variable.** Deploy the app once, read its SP id (step 6c), put it in
    `values.local.yml`, and re-deploy — grants apply on the second deploy.

This is shipped as the optional **`governance/`** bundle (a separate DABs bundle with dev/qa/prod
targets). Configure it like the others and deploy per env — it creates+grants instead of the manual
step‑4 DDL / step‑6d SQL:
```bash
cp governance/values.local.example.yml governance/values.local.yml   # set catalog, schema, app_grantee
cd governance
databricks bundle deploy -t <env> --profile <env>
cd ..
```
> Deploy `governance/` **only** in an environment where you want DABs to own the schema/volume — in
> one where they already exist unmanaged, a deploy would conflict; keep the manual SQL there instead.

Its `resources/uc.yml` is:

```yaml
variables:
  catalog: {}
  schema: {}
  app_grantee:
    description: UC group (recommended) or app SP application-id that gets read access

resources:
  schemas:
    app_schema:
      catalog_name: ${var.catalog}
      name: ${var.schema}
      grants:
        - principal: ${var.app_grantee}
          privileges: [USE_SCHEMA, SELECT]     # SELECT here covers every table in the schema
      lifecycle: { prevent_destroy: true }     # don't drop the schema (and its data) on `bundle destroy`
  volumes:
    artifacts:
      catalog_name: ${var.catalog}
      schema_name: ${var.schema}
      name: artifacts
      grants:
        - principal: ${var.app_grantee}
          privileges: [READ_VOLUME]            # add WRITE_VOLUME if users upload artifacts via UC
      lifecycle: { prevent_destroy: true }
```

`USE CATALOG` on a shared/pre-existing catalog stays a one-line manual grant (or add a bundle-managed
`catalogs:` resource grant if the bundle owns the catalog). Note that a bundle-managed schema/volume is
**owned by the bundle** — `bundle destroy` would drop it, so keep `prevent_destroy: true` on for qa/prod
(and be aware the bundle now reconciles those UC objects on every deploy).

> **Simpler alternative that never takes ownership of UC objects:** keep the idempotent `GRANT` SQL from
> step 6d in a script and run it once per environment (safe to re-run). Bundle-managed grants pay off
> mainly when you're standing up many environments and want a single declarative source of truth.

---

## 7. Verify

```bash
databricks apps get <APP_NAME> --profile <PROFILE> -o json   # app_status.state == RUNNING; note `url`
databricks apps logs <APP_NAME> --profile <OAUTH_PROFILE>    # build/runtime logs (OAuth only)
```
Open the app `url`. Then run a smoke test end to end using **`testing/README.md`** (TC1): build the
fixtures with `testing/setup_test_artifacts.py` (pass `<CATALOG>`/`<SCHEMA>`/`artifacts`), deploy the
house-price model, and confirm the row reaches **Complete** with a live lifecycle timeline and the
serving endpoint becomes READY.

---

## 8. Troubleshooting (known gotchas)

| Symptom | Cause / fix |
|---|---|
| `databricks apps logs` fails with an OAuth error | Logs need OAuth — use `<OAUTH_PROFILE>` (a PAT can't read them). |
| App build fails resolving query columns | The type-gen sample tables don't exist or the app SP lacks `SELECT` — do step 6a + grant SELECT, then re-run `apps deploy`. |
| App shows "crashed/exited" right after deploy | Don't build in the start command — `app.yaml` `command` must be start-only (it is). The build runs in the platform build phase during `apps deploy`. |
| Deploy job "Bad Request" on trigger | The deploy job must stay a **single** notebook task with `base_parameters` (the AppKit jobs plugin sends `notebook_params`, which a multi-task job rejects). Keep it as shipped. |
| Endpoint budget policy / description not applied on update | `budget_policy_id` + `description` are **create-time only** on serving endpoints; they apply when the endpoint is first created, not on later config updates. |
| Serving container fails to load the model | The served model's `pip_requirements` are pinned to wrap-time versions; a **user artifact** must be compatible with the pinned `scikit-learn`/framework in `deploy-job/requirements.txt`. Align your training env or update `requirements.txt`. |
| SDK errors about `budget_policy_id` / workload-type enum in the job | `deploy-job/requirements.txt` pins `databricks-sdk` (needs ≥ 0.62 for these); don't downgrade. |
| Deploy row never appears / stale after reload | The app disables the analytics cache and polls; confirm the warehouse is running and the app SP can query it. |

---

## 9. Uninstall / teardown

```bash
# Delete serving endpoints created by end users first (per endpoint):
databricks serving-endpoints delete <endpoint-name> --profile <PROFILE>

# Destroy the bundles (removes the app and the job):
cd app        && databricks bundle destroy -t default --profile <PROFILE> && cd ..
cd deploy-job && databricks bundle destroy            --profile <PROFILE> && cd ..

# Optionally drop data (irreversible):
# DROP TABLE <CATALOG>.<SCHEMA>.model_deployments;
# DROP TABLE <CATALOG>.<SCHEMA>.model_lifecycle_events;
# DROP VOLUME <CATALOG>.<SCHEMA>.artifacts;
```
