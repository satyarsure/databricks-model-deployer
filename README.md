# databricks-model-deployer

A Databricks App (React/AppKit) that lets users hand in an external model artifact from **S3** or a
**Unity Catalog Volume** and publish it to **Databricks Model Serving** — reusing the MLflow
deployment pattern from Genesis Workbench.

A parameterized workflow job wraps the artifact as an MLflow **pyfunc**, registers it to **Unity
Catalog**, validates it, and creates/updates a **serving endpoint** (A/B traffic split, CPU/GPU
sizing, scale-to-zero, inference tables, and a serverless **budget policy** for chargeback).
Deployment status and full **model lifecycle history** are tracked in Delta tables, with the active
version marked by a UC `@champion` alias.

## Architecture

```
React/AppKit app (satya-takeda-poc-app)
 ├─ Deployed Models tab  → reads the model_deployments Delta table (search, status, "Open" links,
 │                          click a model name to deploy a new version with fields pre-filled)
 └─ Deploy Model tab     → POST /api/deploy → triggers the deploy job
                                                   │
Databricks Job "mlops_deploy_model_job" (DABs, serverless) — one notebook, three stages:
   Wrapper    → load artifact(s), wrap as MLflow pyfunc, build signature, register each A/B variant to UC
   Validator  → load the registered pyfunc, smoke-test predict, optional mlflow.evaluate vs an eval dataset
   Deployer   → create/update the serving endpoint (traffic split, compute, scale-to-zero, tags,
                budget policy, inference tables); set the UC @champion alias
   (every stage writes status to model_deployments and appends to model_lifecycle_events)
```

## Repository layout

| Path | Description |
|------|-------------|
| `satya-takeda-poc-app/` | The React/AppKit app (frontend + Express server). Deployed to Databricks Apps. |
| `deploy-job/` | The DABs bundle for the deploy workflow job (`src/notebooks/deploy_model.py`). |
| `Images/` | UI mockups. |

## Data model (Unity Catalog: `satya_takeda_poc.mlops_test_20260829`)

- **`model_deployments`** — one row per deployment (name, UC name, version, status/stage, endpoint,
  compute, tags, schemas, budget policy, timestamps). Source of truth for the Deployed Models list.
- **`model_lifecycle_events`** — append-only audit trail of every stage/status transition.
- **`artifacts`** volume — holds uploaded/test model artifacts.

## Deploy

Both components use the `DEFAULT1` Databricks CLI profile and live under
`/Workspace/Users/<you>/Takeda/mlops_test_20260829` in the workspace.

```bash
# 1) Deploy job (creates/updates mlops_deploy_model_job)
cd deploy-job
databricks bundle deploy --target dev --profile DEFAULT1

# 2) App — Databricks Apps compute runs `npm install` + build at startup
cd ../satya-takeda-poc-app
databricks bundle deploy -t default --profile DEFAULT1
databricks apps deploy satya-takeda-poc-app \
  --source-code-path /Workspace/Users/<you>/Takeda/mlops_test_20260829/app/files \
  --profile DEFAULT1
```

> Note: local `npm install`/build may be blocked by network policy on some machines; the Databricks
> Apps runtime installs dependencies and builds the app on its own compute. App logs require an OAuth
> profile: `databricks apps logs satya-takeda-poc-app -p <oauth-profile>`.

## Chargeback

- **Serving endpoint** — the form's *Serverless usage policy* (a budget policy ID) becomes the
  endpoint `budget_policy_id`; the *Description* and *Tags* are applied at creation.
- **Deploy job** — carries its own `budget_policy_id` + tags (configurable via `deploy-job` bundle
  variables) so the deployment compute is attributed.

## Credits

Model deployment logic adapted from the
[Genesis Workbench](https://github.com/databricks-industry-solutions/genesis-workbench) solution.
