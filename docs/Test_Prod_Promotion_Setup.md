# Test & Prod Setup + Dev→Test→Prod Promotion (execution runbook)

**Status:** Execution-ready. Items marked `<FILL>` need the **naming-conventions document**
from Prathyusha (catalog/schema names) and are the only blanks. S3 buckets are known.
Steps 1–3 require **workspace-admin / UC-admin** rights (storage credentials + catalog
creation). Steps 4–6 are the bundle/CI work. Step 7 is the change-controlled prod launch.

> **Naming:** "Test" is the bundle target and GitHub environment **`qa`** everywhere in the code
> (`databricks bundle deploy -t qa`, Actions environment `qa`). There is no `test` target.

There are **two** things to promote, and they move separately:

| What | How | When |
|---|---|---|
| The **Model Deployer platform** (deploy job + app) | `bundle-deploy.yml` (or `databricks bundle deploy -t qa`) | Once per environment, then on each platform release |
| A **model** (e.g. PO / Protocol-Intelligence) | `promote-model.yml`, or the app's **UC model (promote)** source | Per model version |

## Inputs we already have
| Env | S3 bucket (from Prathyusha) |
|---|---|
| Test | `arn:aws:s3:::tpc-aws-ted-tst-rnd-protocoloptz-nongxp-us-east-1` |
| Prod | `arn:aws:s3:::tpc-aws-ted-prd-rnd-protocoloptz-nongxp-us-east-1` |

Dev (already working): catalog `usdev_rnd_non_gxp`, schema `rnd_us_mart_po`,
volume `model_deployer_artifacts`. PO model in dev: `usdev_rnd_non_gxp.rnd_us_mart_po.protocol_intelligence_ui`.

**Open question for the admin:** are test and prod catalogs in **separate workspaces** that share
the same Unity Catalog metastore as dev? Promotion copies the model across catalogs, so the target
workspace must be able to **read the source catalog** (Step 3c). If dev and test do not share a
metastore, use the fallback at the end of Step 6.

---

## Step 1 — Storage credential (admin, per account/region, once)
A UC **storage credential** wraps an AWS IAM role that can read/write the buckets.
Create the IAM role per Databricks' UC trust policy, then:
```sql
CREATE STORAGE CREDENTIAL IF NOT EXISTS protocoloptz_nongxp_cred
  WITH IAM ROLE 'arn:aws:iam::<ACCOUNT_ID>:role/<UC_STORAGE_ROLE>'
  COMMENT 'Protocol-Optimization non-GxP buckets (test+prod)';
```
> The role's trust policy must allow the Databricks UC principal and the external ID
> from `DESCRIBE STORAGE CREDENTIAL`. Admin task.

## Step 2 — External locations (admin, one per bucket per env)
```sql
-- TEST
CREATE EXTERNAL LOCATION IF NOT EXISTS protocoloptz_tst
  URL 's3://tpc-aws-ted-tst-rnd-protocoloptz-nongxp-us-east-1/'
  WITH (STORAGE CREDENTIAL protocoloptz_nongxp_cred)
  COMMENT 'Protocol-Opt test artifacts';

-- PROD
CREATE EXTERNAL LOCATION IF NOT EXISTS protocoloptz_prd
  URL 's3://tpc-aws-ted-prd-rnd-protocoloptz-nongxp-us-east-1/'
  WITH (STORAGE CREDENTIAL protocoloptz_nongxp_cred)
  COMMENT 'Protocol-Opt prod artifacts';
```
Verify: `LIST 's3://tpc-aws-ted-tst-rnd-protocoloptz-nongxp-us-east-1/'`

## Step 3 — Catalogs, schemas, volumes, grants (admin; names per naming doc)

**3a. Objects**
```sql
-- TEST  (names: <FILL> from Prathyusha's naming conventions)
CREATE CATALOG IF NOT EXISTS <TEST_CATALOG>
  MANAGED LOCATION 's3://tpc-aws-ted-tst-rnd-protocoloptz-nongxp-us-east-1/';
CREATE SCHEMA  IF NOT EXISTS <TEST_CATALOG>.<TEST_SCHEMA>;
CREATE VOLUME  IF NOT EXISTS <TEST_CATALOG>.<TEST_SCHEMA>.model_deployer_artifacts;

-- PROD
CREATE CATALOG IF NOT EXISTS <PROD_CATALOG>
  MANAGED LOCATION 's3://tpc-aws-ted-prd-rnd-protocoloptz-nongxp-us-east-1/';
CREATE SCHEMA  IF NOT EXISTS <PROD_CATALOG>.<PROD_SCHEMA>;
CREATE VOLUME  IF NOT EXISTS <PROD_CATALOG>.<PROD_SCHEMA>.model_deployer_artifacts;
```

**3b. Target-environment grants** — to the identity that **runs the deploy job** in that
environment (the deploy service principal):
```sql
GRANT USE CATALOG ON CATALOG <TEST_CATALOG> TO `<qa-deploy-sp>`;
GRANT USE SCHEMA, CREATE MODEL, CREATE TABLE ON SCHEMA <TEST_CATALOG>.<TEST_SCHEMA> TO `<qa-deploy-sp>`;
GRANT READ VOLUME, WRITE VOLUME ON VOLUME <TEST_CATALOG>.<TEST_SCHEMA>.model_deployer_artifacts TO `<qa-deploy-sp>`;
-- repeat for PROD with <prod-deploy-sp>
```
(`CREATE TABLE` is for the endpoint's AI Gateway inference tables.)

**3c. Promotion read grants** — each environment reads the model from the one below it:
```sql
-- qa deploy SP reads the dev model
GRANT USE CATALOG ON CATALOG usdev_rnd_non_gxp TO `<qa-deploy-sp>`;
GRANT USE SCHEMA ON SCHEMA usdev_rnd_non_gxp.rnd_us_mart_po TO `<qa-deploy-sp>`;
GRANT EXECUTE ON MODEL usdev_rnd_non_gxp.rnd_us_mart_po.protocol_intelligence_ui TO `<qa-deploy-sp>`;
-- prod deploy SP reads the test model
GRANT USE CATALOG ON CATALOG <TEST_CATALOG> TO `<prod-deploy-sp>`;
GRANT USE SCHEMA ON SCHEMA <TEST_CATALOG>.<TEST_SCHEMA> TO `<prod-deploy-sp>`;
GRANT EXECUTE ON MODEL <TEST_CATALOG>.<TEST_SCHEMA>.protocol_intelligence TO `<prod-deploy-sp>`;
```
(The model grant can also be given in Catalog Explorer → model → Permissions → `EXECUTE`, or for every
model in the schema with `GRANT EXECUTE ON SCHEMA …`.)
If catalogs are **bound to specific workspaces** (workspace-catalog binding), also bind the source
catalog to the target workspace **read-only** (Catalog Explorer → catalog → Workspaces →
assign, *Read only*). Quick check from a notebook in the target workspace:
`SHOW SCHEMAS IN usdev_rnd_non_gxp` must succeed.

## Step 4 — Platform prerequisites + bundle values for `qa` / `prod`
Per environment, before the first bundle deploy (INSTALL.md §4): a **Lakebase project** (the app's
status store, e.g. `rnd-model-deployer-qa`), a **serverless usage policy**, and the **deploy service
principal**. Then fill the `qa:` / `prod:` blocks of each bundle's `values.local.yml` from the
`*/values.local.example.yml` templates. They hold the root path, catalog/schema, experiment, budget
policy, tags, `lakebase_project`, `pg_host`, `app_sp` and (app) `job_id`. Keep them in the secrets
tooling (Step 5), not in git.

Deploy (admin or CI, job first, then the app; INSTALL.md §5–6):
```bash
cd deploy-job && databricks bundle validate -t qa && databricks bundle deploy -t qa && cd ..
cd app && databricks bundle deploy -t qa && cd ..
```
First time only: the app needs the job's id (`job_id`) and the job needs the app's SP (`app_sp`).
Deploy the job, deploy the app, then fill both values and redeploy (INSTALL.md §6c).
Repeat with `-t prod` for production (Step 7, change-controlled).

## Step 5 — GitHub environments, secrets, and variables (turns CI on)
The workflows in `.github/workflows/` **skip gracefully** until the `dev` environment secrets exist.
In **GitHub → Settings → Environments**, create `dev`, `qa`, `prod`.

**Secrets** (each environment) — used by `bundle-ci.yml`, `bundle-deploy.yml`, `promote-model.yml`:

| Secret | Value |
|---|---|
| `DATABRICKS_HOST` | workspace URL for that env |
| `DATABRICKS_CLIENT_ID` | deploy service-principal OAuth client id |
| `DATABRICKS_CLIENT_SECRET` | deploy SP OAuth secret |
| `DEPLOY_JOB_VALUES_LOCAL_YML` | full contents of that env's `deploy-job/values.local.yml` |
| `GOVERNANCE_VALUES_LOCAL_YML` | full contents of `governance/values.local.yml` |
| `APP_VALUES_LOCAL_YML` | full contents of `app/values.local.yml` |

**Variables** (`qa` and `prod` only) — used by `promote-model.yml`:

| Variable | `qa` | `prod` |
|---|---|---|
| `UC_CATALOG` / `UC_SCHEMA` | `<TEST_CATALOG>` / `<TEST_SCHEMA>` | `<PROD_CATALOG>` / `<PROD_SCHEMA>` |
| `SOURCE_UC_CATALOG` / `SOURCE_UC_SCHEMA` | `usdev_rnd_non_gxp` / `rnd_us_mart_po` | `<TEST_CATALOG>` / `<TEST_SCHEMA>` |
| `DEPLOY_JOB_ID` *(optional)* | qa deploy-job id | prod deploy-job id |
| `ENDPOINT_SUFFIX` *(optional)* | e.g. `_qa` if qa shares a workspace with dev | — |

Add **required reviewers** on `qa` and `prod` so every promotion waits for an approval (maps to
Takeda change control).

## Step 6 — Promote the PO model dev → test
**Option A — CI (recommended, auditable):** GitHub → Actions → **Promote Model** → Run workflow:
`spec = promotions/protocol_intelligence.json`, `environment = qa`, `source_version` blank
(= `champion`; enter `1` if `@champion` was never set on the dev model). After approval, the
workflow:
1. runs the **qa** deploy job with a *UC model* variant. The job copies
   `usdev_rnd_non_gxp.rnd_us_mart_po.protocol_intelligence_ui@champion` unchanged into
   `<TEST_CATALOG>.<TEST_SCHEMA>.protocol_intelligence` with `copy_model_version`. That keeps the
   same files, signature and requirements, and tags the copy `promoted_from_*`.
2. creates/updates `protocol_intelligence_endpoint` in the test workspace.
3. queries it with `{"inputs": ["Pregnancy Test", "EKG"]}` and **fails unless** the reply is
   `{"predictions": ["non-invasive", "non-invasive"]}`.
4. writes a run summary (source/target versions, run link, endpoint reply) — attach it to the ticket.

The deployment also appears in the test app's **Deployed Models** with its lifecycle timeline.

**Option B — the app UI (same mechanism):** in the **test** Model Deployer app → Deploy → Artifact
**UC model (promote)** → Source model `usdev_rnd_non_gxp.rnd_us_mart_po.protocol_intelligence_ui`,
Version `champion` → contract **Sample input/output** (above) → UC model
`<TEST_CATALOG>.<TEST_SCHEMA>.protocol_intelligence` → Deploy. (The UI check is non-fatal; read
the deployer's *endpoint check OK* event.)

**Rehearse it now, in dev:** Option B inside the dev app, promoting into a second model name in the
same schema (`testing/README.md` **TC14a**). This proves the copy + serve path before test exists.

**Fallback (no shared metastore):** copy the dev MLflow model folder (`.../model_deployer_artifacts/pi-model`)
into the test volume and deploy it as **UC Volume** (as-is), exactly as it was done in dev.

## Step 7 — Prod launch (change-controlled)
1. Change request raised. Draft content: `docs/Production_Change_Request_PO.md`.
2. Evidence from test: the Step 6 run summary (`SUCCESS`, endpoint reply matching), the bundle
   deploy run for `qa`, and the reviewer approval.
3. Approval recorded → **Bundle Deploy** (`environment = prod`) for the platform, then
   **Promote Model** with `environment = prod` and the `change_request` id (required for prod;
   it is written onto the endpoint as a tag).
4. Post-deploy: the workflow's re-query is the first check; also query from the consuming
   application's identity (`docs/ServicePrincipal_REST_Security.md`).
5. Rollback: redeploy the previous version (app → model → **New version** with an existing
   version, or re-run the workflow with `source_version` = the prior version). The platform is
   rolled back by redeploying the previous commit (bundles are declarative).

---

*Only blanks are the `<FILL>` names (awaiting Prathyusha's naming doc) and the SP names. Everything
else is ready to run by an admin / CI.*
