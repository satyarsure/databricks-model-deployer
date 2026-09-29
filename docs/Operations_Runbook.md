---
title: "Model Deployer — Operations Runbook"
subtitle: "Running, promoting, and supporting the Model Deployer after handover"
date: "September 2026"
---

This runbook is for the team that **operates and supports** the Model Deployer. End users deploying
models should read the **User Guide** (`docs/Model_Deployer_User_Guide.md`); first-time installation
is in **INSTALL.md**. Placeholders in `<angle brackets>` stand for your environment's values.

# 1. What runs where

| Component | What it is | Where to look |
|---|---|---|
| **App** | Databricks App (React UI + Express server). Users fill the Deploy form; the board shows status. | Compute → Apps → `<APP_NAME>` (logs, deployments, permissions) |
| **Deploy job** | Serverless job `mlops_deploy_model_job` running one notebook: wrapper → validator → deployer. | Jobs → `mlops_deploy_model_job` → Runs (each deployment is one run) |
| **Lakebase Postgres** | The app's operational store: `model_deployments`, `model_lifecycle_events`, `model_deployment_drafts` in schema `<PG_SCHEMA>`. | `databricks psql --project <LB_PROJECT> …` (section 6) |
| **Unity Catalog** | Registered models (`<catalog>.<schema>.<model>`) with the `@champion` alias, and the `artifacts` volume. | Catalog Explorer → the model → Versions (tags show provenance) |
| **Serving endpoints** | One per model (`<model>_endpoint`), created by the job. AI Gateway usage tracking + inference tables on. | Serving → the endpoint → Events / Build logs / Logs |
| **Secret scope** *(optional)* | Credentials for importing from other workspaces' model registries. | `databricks secrets list-secrets <SCOPE>` |

**Identities.** The **app's service principal** reads/writes Lakebase and triggers the job. The
**job's run identity** does the real work — reads artifacts, registers models, creates endpoints —
so *its* permissions are what count. The app captures the **signed-in user** (for the board, drafts,
and default `CAN_MANAGE` on the endpoint), but deployments do not run as that user.

# 2. Roles

| Role | Does |
|---|---|
| **Platform admin** | Installs/upgrades the app and job; owns the bundle variables per environment; manages the secret scope and source-workspace connections; grants UC access to new teams. |
| **Support (operations)** | Watches failed deployments, triages with section 7, answers user questions, escalates platform issues. |
| **Model owner** | Deploys their model through the app; supplies the model record (section 8); owns the endpoint (`CAN_MANAGE`). |
| **Change approver** | Approves promotions to QA/production per your change-management process (section 5). |

# 3. Configuration

Everything environment-specific is a **bundle variable** in the gitignored `values.local.yml` of
each bundle (full list and meaning: INSTALL.md §5 and §6a). The ones operations most often change:

| Variable (bundle) | Default | Change it when |
|---|---|---|
| `max_concurrent_deployments` (deploy-job) | `5` | Users routinely see deployments **queued**. Extra deployments wait and start automatically; none are dropped. |
| `source_registry_secret_scope` (deploy-job) | blank | Turning on imports from another workspace's model registry (INSTALL.md §6f). |
| `budget_policy_id` (both) | — | Chargeback moves to a different serverless usage policy. Endpoints keep the policy they were created with. |
| `resource_tags` (deploy-job) | `application` | Governance/chargeback tags change. Applied to the job and to every endpoint on its next deployment. |
| `experiment` (deploy-job) | blank | Where deployments log MLflow runs by default (`<experiment>/<model name>`). |

Apply a change by editing `values.local.yml` and redeploying that bundle
(`databricks bundle deploy -t <env> --profile <env>`). No app restart is needed for deploy-job
variables; they take effect on the next deployment.

# 4. Routine tasks

**Upgrade to a new release.** Deploy the **job first, then the app**, per environment:
```bash
cd deploy-job && databricks bundle deploy -t <env> --profile <env> && cd ..
cd app && databricks bundle deploy -t <env> --profile <env> \
  && databricks apps deploy <APP_NAME> --source-code-path <ROOT_PATH>/app/files --profile <env> && cd ..
```
New Lakebase columns are added automatically on the next deployment; the board tolerates the gap.

**Onboard a new team.** Give the team `CAN_USE` on the app, and give the **job's run identity** (not
the users) `USE CATALOG`/`USE SCHEMA`/`CREATE MODEL` on the target schema and read access to where
their artifacts live. Users who upload files need `WRITE VOLUME` on the artifacts volume.

**Connect a source workspace** for Workspace-registry imports: INSTALL.md §6f. **Rotate** its token by
overwriting `<prefix>-token` (`databricks secrets put-secret <SCOPE> <prefix>-token`) before it
expires. **Disconnect** it by deleting both `<prefix>-host` and `<prefix>-token`.

**Clean up** a retired model: delete its serving endpoint, then (if policy allows) the UC model.
Deployment history stays in Lakebase for audit.

# 5. Promotion: dev → QA → production

**Principle:** promote the **same commit** to each environment; only the target (`-t`), the profile
(workspace), and that environment's `values.local.yml` differ. Keep each environment's values in your
secrets tooling, not in git.

**Change-management checklist** (follow your organization's process — ticket and approvals are
required for every environment above dev, including new "greenfield" ones):

1. Change ticket raised, referencing the commit/tag being promoted.
2. Evidence from the lower environment: `bundle validate` output, and a passing smoke test
   (testing/README.md **TC1**, plus **TC12** if model imports changed) with the run links.
3. Approval recorded on the ticket.
4. Deploy job, then app (section 4) to the target environment.
5. Post-deploy check: app status `SUCCEEDED` (`databricks apps get <APP_NAME>`), then one TC1 deploy
   reaching **Complete** with **endpoint check OK**. Attach to the ticket.
6. Rollback plan: redeploy the previous commit the same way (bundles are declarative).

**Automating it.** If your CI runs the Databricks CLI (e.g. GitHub Actions or Azure DevOps), the
pipeline is the same commands. An example GitHub Actions workflow — adapt names and secrets to your
setup; per-environment approvals come from GitHub *environments* with required reviewers:

```yaml
name: promote-model-deployer
on:
  workflow_dispatch:
    inputs:
      target:
        description: dev | qa | prod
        required: true
jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: ${{ inputs.target }}   # required reviewers on qa / prod
    env:
      DATABRICKS_HOST: ${{ vars.DATABRICKS_HOST }}
      DATABRICKS_CLIENT_ID: ${{ secrets.DATABRICKS_CLIENT_ID }}          # deployment service principal
      DATABRICKS_CLIENT_SECRET: ${{ secrets.DATABRICKS_CLIENT_SECRET }}
    steps:
      - uses: actions/checkout@v4
      - uses: databricks/setup-cli@main
      - name: Write this environment's values (kept in secrets, never in git)
        run: |
          printf '%s' "$JOB_VALUES" > deploy-job/values.local.yml
          printf '%s' "$APP_VALUES" > app/values.local.yml
        env:
          JOB_VALUES: ${{ secrets.DEPLOY_JOB_VALUES_YML }}
          APP_VALUES: ${{ secrets.APP_VALUES_YML }}
      - name: Deploy job, then app
        run: |
          (cd deploy-job && databricks bundle validate -t "${{ inputs.target }}" && databricks bundle deploy -t "${{ inputs.target }}")
          (cd app && databricks bundle deploy -t "${{ inputs.target }}")
          databricks apps deploy "${{ vars.APP_NAME }}" --source-code-path "${{ vars.APP_ROOT_PATH }}/app/files"
```

# 6. Monitoring and health checks

- **The board** (Deployed Models tab) is the first stop: status, the stage that failed, and the
  lifecycle timeline, including **endpoint check OK / failed** after each deployment.
- **Job run output** (Jobs → the run) has the full log: which artifact was loaded, the requirements
  of imported models, waits on in-progress endpoint updates, and stack traces.
- **Endpoint pages** (Serving → endpoint): *Events* for rollout, *Build logs* when the container
  fails to build (common for old imported models), *Logs* for errors at request time.
- **Usage and payloads**: AI Gateway usage tracking, and inference tables
  `<catalog>.<schema>.<endpoint>_payload*`. Each deployment's endpoint check adds one row.
- **Recent failures** straight from Lakebase:
  ```bash
  databricks psql --project <LB_PROJECT> --profile <PROFILE> -- -c \
    "SELECT deployment_id, model_name, stage, left(error_message, 200) AS error, deployed_by, updated_at
       FROM <PG_SCHEMA>.model_deployments
      WHERE status = 'FAILED' AND updated_at > now() - interval '7 days'
      ORDER BY updated_at DESC;"
  ```

# 7. Triage playbook

| Symptom | Likely cause | What to do |
|---|---|---|
| Failed at **wrapper** — "Unsupported artifact", unpickling error, path not found | Wrong path, a non-model file, or a pickle built with an incompatible library version | Check the path and file; the pickle must match the pinned versions in `deploy-job/requirements.txt`. |
| Failed at **wrapper** — "not enabled here" / "No credentials for …" | Import from another workspace not set up | INSTALL.md §6f; the message names the scope and secret keys. |
| Failed at **wrapper** — "Unity Catalog needs a model signature" | No usable contract | The user must fill the model contract (schema, or sample input + output). |
| **Complete**, but **endpoint check failed** | The served model doesn't accept the example — often a bare classifier deployed without its preprocessing | Deploy the full model (Workspace registry / MLflow folder), or fix the contract/sample. |
| Endpoint stuck or failed to build (imported model) | The model's own requirements don't install on Model Serving (very old Python/package pins) | Read *Build logs*; re-log the model with current versions in its source workspace and import that version. |
| Failed at **deployer** — quota, permission, or "being updated" | Serving quota/permissions, or a concurrent update | Quota/permissions: platform admin. Concurrent updates are now waited out; if it recurs, re-run. |
| Deployments queued for a long time | More than `max_concurrent_deployments` submitted | Normal; raise the variable if routine (section 3). |
| Board empty / permission errors in app logs | Job never ran in this environment, or `app_sp` not set | INSTALL.md §6c and §8. |

More install-level issues: INSTALL.md §8.

# 8. Model record (reproducibility)

Each model needs a record that lets someone reproduce what is being served: **the artifact, its
parameters, the training data, and the training code**.

**Recorded automatically**

| What | Where |
|---|---|
| Exact artifact that was served | The UC model version (and its MLflow run's `model/` artifacts) |
| Input/output contract | The version's signature; the deployment row (`input_schema_json`, `sample_input_json`, …) |
| Who deployed what, when, and every stage transition | Lakebase `model_deployments` + `model_lifecycle_events` |
| Serving configuration (compute, traffic split, tags, policy) | The deployment row and the endpoint |
| **Imported models:** source workspace/model/version/run | UC version tags `source_*`; run tags `model_deployer.source_*` |
| **Imported models:** source run's params, final metrics, training-code location, git commit, dataset inputs (when the source run logged them) | The import run's params/metrics and `model_deployer.source_*` tags |

**Supplied by the model owner** (for pickled files, where nothing about training travels with the
file) — put them in the Deploy form's **Tags** so they land on the endpoint and the deployment row:
`training_data` (table/path **and** version or snapshot date), `code_repo` + `code_commit`,
`hyperparameters` (or where they're recorded), `owner`, and `validation_report`. Keep the full
training package (code, environment file, data snapshot reference) wherever your model owners keep
project deliverables, and reference it in the tags.

# 9. Security notes

- The **job's run identity** reads artifacts and registers models for everyone who can use the app.
  Keep the app's `CAN_USE` limited to people allowed to deploy, and the identity's grants limited to
  the catalogs/schemas the app should publish to.
- **Cross-workspace imports** use a stored token for a source-workspace service principal: grant it
  read on only the models being migrated, keep the token short-lived, rotate it, and remove it when
  the migration ends (INSTALL.md §6f).
- Endpoint access comes from the form's permissions plus the submitter's default `CAN_MANAGE`. Review
  endpoint ACLs as part of access reviews.

# 10. Known limits

- **No direct upload from a user's computer:** upload to a UC Volume, then deploy from the Volume.
- **S3 sources** use the job's default AWS credentials; confirm the job's compute can reach the
  bucket before relying on it.
- **Budget policy and description** of an endpoint are set at creation only; tags are re-synced.
- Endpoints hold **at most 20 tags**; lowest-priority automatic tags are dropped first.
