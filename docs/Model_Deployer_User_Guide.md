---
title: "Model Deployer — User Guide"
subtitle: "How to deploy and manage models with the Model Deployer app"
date: "September 2026"
---

# 1. What is Model Deployer?

Model Deployer is a Databricks App that turns a trained model artifact into a live
**Model Serving endpoint** — without writing deployment code. You point it at a model file
(or an already-registered model version), describe the inputs and outputs, choose the compute,
and click deploy. Behind the scenes it runs a three-stage pipeline:

1. **Wrapper** — loads your artifact, wraps it as an MLflow model with a signature built from your
   input/output schema, and registers it as a new version in Unity Catalog.
2. **Validator** — loads the registered model, runs a smoke-test prediction, and (optionally)
   scores it against an evaluation dataset with `mlflow.evaluate`.
3. **Deployer** — creates or updates the Model Serving endpoint (traffic split, compute,
   scale-to-zero, tags, usage policy, inference tables) and sets the `@champion` alias.

Everything you deploy shows up on the **Deployed Models** board with a live status timeline, so you
can watch each stage complete (or see exactly where it failed).

# 2. Accessing the app

Open the app URL in your browser (your administrator provides it — it looks like
`https://<app-name>-<id>.<region>.databricksapps.com`). You sign in with your Databricks
workspace credentials; the app shows your signed-in email in the header.

You do **not** need cluster, notebook, or CLI access to use the app — everything happens through
the web form. You do need permission to register models in the target Unity Catalog schema and to
create serving endpoints (your administrator configures this).

# 3. The interface

The app has two tabs:

| Tab | What it's for |
|---|---|
| **Deployed Models** | The dashboard of everything that has been (or is being) deployed, with live status, the lifecycle timeline, and actions (A/B test, new version). |
| **Deploy Model** | The form you fill in to start a new deployment. |

# 4. Deploying a model — step by step

1. Go to the **Deploy Model** tab.
2. Fill in the form (each field is explained in section 5).
3. Click **Save & Deploy**.
4. You are taken to **Deployed Models**, where a new row appears **immediately** as
   *Deploy in progress* with a "submitted" event — no waiting for compute to warm up.
5. The row streams its lifecycle: **wrapper → validator → deployer**. When it finishes it turns
   green **Complete** and a **Serving → Open** link appears.

That's it. A typical CPU/SMALL deployment takes a few minutes (most of it is the serving endpoint
becoming ready).

# 5. Field reference

| Field | Required | What to enter |
|---|---|---|
| **Model Name** | Yes | A friendly name for the deployment (e.g. `house-price-regressor`). Shown on the board. |
| **Description** | No | Free text describing the model. |
| **Artifact** | Yes | Where the model file lives — a **UC Volume** path or an **S3** path (see section 6). For A/B tests you can add more than one, or reference an existing version. |
| **Model contract** | Yes | How you describe inputs/outputs (see section 7): **Columnar schema** (tabular models) or **Sample input / output** (text / JSON / tensor models). |
| **Input / Output schema** *(schema mode)* | — | JSON arrays of the model's columns (see section 7). |
| **Sample input / output** *(sample mode)* | — | A real request/response example, e.g. `{"instances": [...]}` / `{"predictions": [...]}` (see section 7). |
| **Experiment name** | No | MLflow experiment path to log to. **Leave blank** to use the environment's configured default. |
| **Evaluation dataset** | No | A CSV/Parquet path to score the model against (see section 9). Leave blank to skip. |
| **UC Model name** | Yes | The Unity Catalog three-level name the model is registered under: **Catalog**, **Schema**, **Model**. |
| **Serverless usage policy** | No | A budget/usage policy ID for endpoint cost tracking. **Leave blank** to use the environment's configured default. |
| **Compute** | Yes | CPU or GPU, size (SMALL / MEDIUM / LARGE), and scale-to-zero on/off (see section 8). |
| **Tags** | No | A JSON object of extra tags for the endpoint (see section 10). |
| **Endpoint permissions** | No | JSON granting others access to the endpoint (see section 11). |

> **Note on Experiment and Serverless usage policy:** both are optional. When you leave them blank,
> the deployment uses the defaults your administrator configured for the environment. Fill them in
> only to override for a single deployment.

# 6. Choosing an artifact

Each deployment has one or more **variants**. A variant is one of:

- **New artifact** — a model file that gets wrapped and registered as a new version. Choose the
  source type and enter the path:
  - **UC Volume**: `/Volumes/<catalog>/<schema>/<volume>/path/to/model.pkl`
  - **S3**: `s3://bucket/path/to/model.pkl`
- **Existing version** — an already-registered version of the same Unity Catalog model. It is
  served **as-is** (no re-wrapping). Used for champion-vs-challenger A/B tests (section 13).

Supported artifact formats are standard pickled model files (e.g. scikit-learn, XGBoost, LightGBM).

# 7. Defining the model contract (schema **or** sample)

Every model must have a contract so it can be registered and served. Pick one of two modes with the
**Model contract** selector on the form. (Unity Catalog requires a signature, so you can't deploy
with no contract at all.)

## Mode A — Columnar schema (tabular models)

Enter an **Input schema** and **Output schema** as JSON arrays; each entry is
`{ "name": "<field>", "type": "<type>" }`. Use `double` for continuous features/regression outputs
and `long` for integer/class outputs (this also tells the validator whether to score as a classifier
or regressor).

Input schema — the model's features (order matters):
```json
[
  { "name": "sqft", "type": "double" },
  { "name": "bedrooms", "type": "double" },
  { "name": "bathrooms", "type": "double" },
  { "name": "age", "type": "double" }
]
```
Output schema:
```json
[ { "name": "price", "type": "double" } ]
```

## Mode B — Sample input / output (text, JSON, tensor models)

For models whose contract is an array/JSON payload rather than named columns — e.g. a text
classifier invoked as `{"instances": ["Pregnancy Test", "EKG"]}` — switch to **Sample input /
output** and paste a **real example**:

- **Sample input:** `{"instances": ["Pregnancy Test", "EKG"]}`
- **Sample output:** `{"predictions": ["non-invasive", "non-invasive"]}`

The model signature is **inferred from your example**, so the endpoint serves the model's native
format (you then query it with the same `{"instances": [...]}` shape and get `{"predictions": [...]}`
back). Use this for text/NLP models, and remember the artifact must be a **complete pipeline** (e.g.
vectorizer + classifier) that actually accepts that input — a bare classifier will register but fail
at query time.

# 8. Compute options

| Option | Choices | Notes |
|---|---|---|
| **Compute type** | CPU or GPU | GPU requires serverless GPU serving quota; provisions slower and costs more. |
| **GPU type** | e.g. T4, A10, H100 | Only when Compute type = GPU. |
| **Size** | SMALL / MEDIUM / LARGE | Pick based on model size and expected load. |
| **Scale-to-zero** | On / Off | On = the endpoint sleeps when idle (cheaper; first call after idle has a cold-start delay). Off = always warm. |

# 9. Evaluation dataset (optional)

If you provide an **Evaluation dataset** (a CSV or Parquet path), the validator runs
`mlflow.evaluate` against it. The file's columns must be the model's features **plus a target
column named exactly like your output-schema field** (e.g. `price`, `prediction`, `churn`).

- If the output field is `long`/integer → **classifier** metrics are computed.
- Otherwise → **regressor** metrics.
- If the target column is missing, the rows are just scored (no metrics).

Evaluation is **non-fatal**: a bad eval file logs a warning and the deployment still completes.

# 10. Tags

The **Tags** field is a JSON object of extra key/value tags applied to the serving endpoint:
```json
{ "team": "ds-housing", "project": "pricing" }
```
Your environment also applies a standard set of governance/chargeback tags automatically (e.g.
application, cost center, team, environment). Any tag you enter here is added on top, and **your
value wins** if a key overlaps with a standard tag.

> A serving endpoint can hold **at most 20 tags total**. If the combined set (your tags + the
> environment's governance tags + a couple of automatic ones) exceeds 20, your **form tags and the
> governance tags are kept first**, and the auto-added ones (`deployed_by`, `gpu_type`, `application`)
> are dropped to fit — so your important tags always land.

# 11. Endpoint permissions (optional)

By default, only you (the person deploying) get **CAN_MANAGE** on the new endpoint. To grant others
access, enter a JSON object in the **Endpoint permissions** field:

```json
{
  "can_manage": ["someone@company.com"],
  "can_query":  ["service-account@company.com"],
  "can_view":   ["viewer@company.com"]
}
```

- Keys are `can_manage`, `can_query`, `can_view`; each is a list of principals.
- A principal is recognized by shape: contains `@` → user, a 36-character UUID → service principal,
  otherwise a group name.
- Leave the field blank to keep the endpoint private to you.
- The person deploying always keeps CAN_MANAGE.

# 12. Monitoring a deployment

On the **Deployed Models** board each row shows:

- **Model name** (click it to deploy a new version — see section 13), **UC name**, **Version**,
  **Deploy Date** (full date and time), **Status**, and **Serving** actions.
- A **status badge**: amber **Deploy in progress**, green **Complete**, or red **Failed**.
- A **collapsible lifecycle timeline** (click the chevron). While in progress it shows a live
  indicator and advances through the stages, each with a status and timestamp:
  *submitted → wrapper → validator → deployer → complete.*

Other board features:

- **Search** — filter rows by model name or UC name (contains match).
- **Resizable columns** — drag a column header's right edge to resize; widths are remembered in your
  browser.
- A new deployment's row appears **instantly** as *submitted* (no cold-start gap), then updates in
  place as the job runs.

# 13. Deploying a new version

To deploy a newer artifact for a model you already deployed:

1. On **Deployed Models**, click the **model name**.
2. The Deploy form opens **pre-filled**, with Model name, Experiment, and UC model **locked**.
3. Change the **artifact** to the new file (and any other settings you want).
4. Click **Deploy new version**.

The model gets a new registered version, the **same endpoint is updated in place** (no downtime),
and `@champion` moves to the new version.

# 14. A/B testing

You can serve two variants of the same model on one endpoint with a traffic split.

**Two new artifacts:**

1. On the Deploy form, click **Add artifact (A/B variant)** so there are two variants.
2. Set each variant's **Traffic %** — the footer must read **Traffic total: 100%** before you can
   save.
3. Deploy. Both variants are registered and served on one endpoint at the split you chose;
   `@champion` goes to the higher-traffic variant.

**Champion vs. challenger (existing version + new artifact):**

1. On a **Complete** row, click the **A/B test** button (or click the model name).
2. Set one variant to **Existing version** (pick the current champion from the dropdown) and the
   other to **New artifact** (the challenger file). Set the traffic split.
3. Deploy. The existing version is served as-is (not re-wrapped); only the challenger is registered
   as a new version.

# 15. Querying your deployed endpoint

Once a row is **Complete**, click **Serving → Open** for the endpoint, or query it directly. Using
the Databricks CLI:

**Schema-mode model** — send records matching your input schema:
```
databricks serving-endpoints query <endpoint_name> \
  --json '{"dataframe_records":[{"sqft":2200,"bedrooms":3,"bathrooms":2,"age":8}]}' \
  --profile <your-profile>
```

**Sample-mode model** — query with the same shape as your sample input:
```
databricks serving-endpoints query <endpoint_name> \
  --json '{"instances": ["Pregnancy Test", "EKG"]}' \
  --profile <your-profile>
# → {"predictions": ["non-invasive", "non-invasive"]}
```

The endpoint name defaults to `<model>_endpoint`.

# 16. When a deployment fails

If a deployment turns **Failed**, expand the row's lifecycle timeline — the last event is a red
**FAILED** entry with the error message and the stage that failed:

| Stage that failed | Common cause |
|---|---|
| **Wrapper** | The artifact couldn't be loaded (wrong path, not a model file, unpickling error), or (schema mode) the model couldn't be registered — Unity Catalog needs a valid signature. |
| **Deployer** | The serving endpoint could not be created/updated (permissions, quota, or configuration). |

Note: the **validation smoke test is non-fatal** — if it can't exercise the model with the example
(common for text/sample-mode models), it's noted on the timeline as a warning and the deployment
**still proceeds to serving**. It won't, by itself, mark the deployment Failed.

Fix the underlying issue and deploy again. A failed deployment does **not** create an endpoint.

# 17. Tips

- **Match the input schema to the model exactly** (schema mode) — the most common failure is
  declaring the wrong number or order of features.
- For **text / JSON / tensor** models, use **Sample input / output** mode instead of a schema, and
  make sure the artifact is a **complete pipeline** (e.g. vectorizer + classifier) that accepts the
  sample you paste.
- Use **`double`** for continuous features and regression outputs; use **`long`** for class labels
  so the validator scores it as a classifier.
- Leave **Experiment** and **Serverless usage policy** blank unless you specifically need to
  override the environment defaults.
- For A/B tests, remember the traffic must total **100%** before Save is enabled.
- The first call to a **scale-to-zero** endpoint after it's been idle has a short cold-start delay;
  turn scale-to-zero **off** for latency-sensitive workloads.
