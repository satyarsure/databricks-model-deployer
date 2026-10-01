# CI/CD — GitHub Actions

Two workflows drive the Databricks Asset Bundles (DABs) in this repo. They implement
the **DABs Project Structure & GitHub Actions Workflow Setup** and use **service-principal
OAuth** auth (Milestone 1.2).

| Workflow | Trigger | What it does |
|---|---|---|
| `bundle-ci.yml` | PR / push touching a bundle | `databricks bundle validate` for all three bundles against `dev` (read-only). |
| `bundle-deploy.yml` | Manual (`workflow_dispatch`) or push to `main` | `databricks bundle deploy` in order: governance (opt-in) → deploy-job → app. |

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
- These workflows have **not been executed** — they need the secrets above and live
  workspace access, which aren't available from the authoring machine. Validate them with
  a first PR run in `dev`.
