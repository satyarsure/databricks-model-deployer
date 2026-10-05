# CI/CD — GitHub Actions

Three workflows drive the Databricks Asset Bundles (DABs) and model promotion in this repo. They implement
the **DABs Project Structure & GitHub Actions Workflow Setup** and use **service-principal
OAuth** auth (Milestone 1.2).

| Workflow | Trigger | What it does |
|---|---|---|
| `bundle-ci.yml` | PR / push touching a bundle | `databricks bundle validate` for all three bundles against `dev` (read-only). |
| `bundle-deploy.yml` | Manual (`workflow_dispatch`) or push to `main` | `databricks bundle deploy` in order: governance (opt-in) → deploy-job → app. |
| `promote-model.yml` | Manual (`workflow_dispatch`), target `qa` / `prod` | Promotes a **model**: runs that environment's deploy job to copy the source UC model version unchanged, serve it, and **fail unless** the endpoint returns the spec's expected output. Specs live in `promotions/`. |

## One-time setup

Create a GitHub **Environment** for each target you deploy to: `dev`, `qa`, `prod`
(Settings → Environments). Add required reviewers on `qa`/`prod` to gate promotion.

In **each** environment add these secrets:

| Secret | Value |
|---|---|
| `DATABRICKS_HOST` | Workspace URL, e.g. `https://onetakeda-us-dev-rnd.cloud.databricks.com` |
| `DATABRICKS_CLIENT_ID` | Deploy **service principal** OAuth client id |
| `DATABRICKS_CLIENT_SECRET` | Deploy service principal OAuth secret |
| `DEPLOY_JOB_VALUES_LOCAL_YML` | Full contents of `deploy-job/values.local.yml` for that env |
| `GOVERNANCE_VALUES_LOCAL_YML` | Full contents of `governance/values.local.yml` for that env |
| `APP_VALUES_LOCAL_YML` | Full contents of `app/values.local.yml` for that env |

`promote-model.yml` also needs these **variables** on the `qa` and `prod` environments (Settings →
Environments → *env* → Variables):

| Variable | Value |
|---|---|
| `UC_CATALOG`, `UC_SCHEMA` | Where the model is promoted **to** (this environment's catalog/schema) |
| `SOURCE_UC_CATALOG`, `SOURCE_UC_SCHEMA` | Where it is promoted **from** (the environment below) |
| `DEPLOY_JOB_ID` *(optional)* | The environment's deploy-job id; looked up by name when unset |
| `ENDPOINT_SUFFIX` *(optional)* | Appended to endpoint names, e.g. `_qa` if two environments share a workspace |

A promotion to `prod` also requires the `change_request` input; it is recorded as an endpoint tag
and in the run summary.

The `*_VALUES_LOCAL_YML` secrets carry the per-environment `values.local.yml` (which is
gitignored). CI writes them to disk before validate/deploy, so **no workspace, catalog,
schema, or policy value is ever committed**. Build each from the matching
`*/values.local.example.yml`.

> The deploy service principal needs: workspace deploy rights, UC `USE CATALOG` +
> (for governance) `CREATE SCHEMA`/volume rights, and permission to create jobs, apps,
> and serving endpoints.

## Deploy order & the `job_id` dependency

`app/values.local.yml` requires `job_id` — the numeric id of the deploy-job. That id
only exists **after** the deploy-job bundle has been deployed once. So the first time you
stand up an environment:

1. Run `bundle-deploy.yml` (governance opt-in as needed) — deploy-job succeeds, app fails or is skipped until `job_id` is set.
2. Get the id: `databricks jobs list` (or the job URL).
3. Put it in that environment's `APP_VALUES_LOCAL_YML` secret.
4. Re-run the workflow — app now deploys.

## Notes / not yet done

- The `databricks/setup-cli@main` step installs the latest CLI. Pin `version:` to your
  workspace's supported release once confirmed.
- `promote-model.yml`'s shell logic was exercised locally against a stubbed CLI (success, failed run,
  transient API error, missing config, prod without a change request), but not on a runner.
- These workflows have **not been executed** — they need the secrets above and live
  workspace access, which aren't available from the authoring machine. Validate them with
  a first PR run in `dev`.
