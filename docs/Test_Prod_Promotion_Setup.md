# Test & Prod Setup + Dev→Test→Prod Promotion (execution runbook)

**Status:** Execution-ready. Items marked `<FILL>` need the **naming-conventions document**
from Prathyusha (catalog/schema names) and are the only blanks. S3 buckets are known.
Steps 1–3 require **workspace-admin / UC-admin** rights (storage credentials + catalog
creation); steps 4–5 are the bundle/CI work.

## Inputs we already have
| Env | S3 bucket (from Prathyusha) |
|---|---|
| Test | `arn:aws:s3:::tpc-aws-ted-tst-rnd-protocoloptz-nongxp-us-east-1` |
| Prod | `arn:aws:s3:::tpc-aws-ted-prd-rnd-protocoloptz-nongxp-us-east-1` |

Dev (already working): catalog `usdev_rnd_non_gxp`, schema `rnd_us_mart_po`,
volume `model_deployer_artifacts`.

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

## Step 3 — Catalogs, schemas, volumes (admin; names per naming doc)
```sql
-- TEST  (names: <FILL> from Prathyusha's naming conventions)
CREATE CATALOG IF NOT EXISTS <TEST_CATALOG>;
CREATE SCHEMA  IF NOT EXISTS <TEST_CATALOG>.<TEST_SCHEMA>;
CREATE VOLUME  IF NOT EXISTS <TEST_CATALOG>.<TEST_SCHEMA>.model_deployer_artifacts;

-- PROD
CREATE CATALOG IF NOT EXISTS <PROD_CATALOG>;
CREATE SCHEMA  IF NOT EXISTS <PROD_CATALOG>.<PROD_SCHEMA>;
CREATE VOLUME  IF NOT EXISTS <PROD_CATALOG>.<PROD_SCHEMA>.model_deployer_artifacts;
```
Grant the **deploy job's run identity** `USE CATALOG`, `USE SCHEMA`, `CREATE MODEL` on
each schema, and `READ VOLUME` / `WRITE VOLUME` on each artifacts volume.

## Step 4 — Bundle targets (per env `values.local.yml`)
Keep these in secrets tooling, never in git. One file per bundle per env; mirror dev.
```yaml
# deploy-job/values.local.yml  (test example)
root_path: /Workspace/Shared/model_deployer
catalog: <TEST_CATALOG>
schema: <TEST_SCHEMA>
experiment: /Shared/model_deployer_experiments
budget_policy_id: <TEST_POLICY_ID>
resource_tags:
  application: model-deployer
  environment: test
max_concurrent_deployments: 5
# app/values.local.yml, governance/values.local.yml — mirror catalog/schema/policy
```
Deploy (admin or CI, job first then app):
```bash
cd deploy-job && databricks bundle validate -t test && databricks bundle deploy -t test && cd ..
cd app && databricks bundle deploy -t test && cd ..
```
Repeat with `-t prod` for production (gated by change control — Step 5).

## Step 5 — CI/CD secrets (makes the drafted GitHub workflows run)
The workflows in `.github/workflows/` currently **skip gracefully** until the `dev`
environment secrets exist. To turn CI on, in **GitHub → Settings → Environments**, create
`dev`, `test`, `prod` and add:

| Secret | Value |
|---|---|
| `DATABRICKS_HOST` | workspace URL for that env |
| `DATABRICKS_CLIENT_ID` | deploy service-principal OAuth client id |
| `DATABRICKS_CLIENT_SECRET` | deploy SP OAuth secret |
| `DEPLOY_JOB_VALUES_LOCAL_YML` | full contents of that env's `deploy-job/values.local.yml` |
| `GOVERNANCE_VALUES_LOCAL_YML` | full contents of `governance/values.local.yml` |
| `APP_VALUES_LOCAL_YML` | full contents of `app/values.local.yml` |

Add **required reviewers** on the `test` and `prod` environments so promotion needs an
approval (maps to Takeda change control). The promote workflow is in
`docs/Operations_Runbook.md` §5.

## Step 6 — Prod launch validation (change-controlled)
1. Change ticket referencing the promoted commit/tag.
2. Evidence from test: `bundle validate` output + one successful deploy through the app
   reaching **Ready** with endpoint check OK (the Protocol-Intelligence model is the
   smoke test — same `{"inputs":["Pregnancy Test","EKG"]}` → `["non-invasive","non-invasive"]`).
3. Approval recorded → `bundle deploy -t prod` (job then app).
4. Post-deploy: query the prod endpoint, confirm predictions, attach to ticket.
5. Rollback: redeploy previous commit (bundles are declarative).

---

*Only blanks are the `<FILL>` names (awaiting Prathyusha's naming doc). Everything else is
ready to run by an admin / CI.*
