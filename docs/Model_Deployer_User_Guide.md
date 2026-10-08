---
title: "Model Deployer — User Guide"
subtitle: "How to deploy and manage models with the Model Deployer app"
date: "October 2026"
---

# 1. What is Model Deployer?

Model Deployer is a Databricks App that turns a trained model into a live **Model Serving
endpoint**, without writing deployment code. You point it at your model — a single model file, an
exported MLflow model folder, or a code folder — describe its inputs and outputs, choose the compute,
and click **Deploy**. Behind the scenes a deployment job runs four steps:

1. **Prepare** — checks your request, reads S3 paths through their Unity Catalog external volume,
   and works out what each artifact is (model file / MLflow model folder / code folder).
2. **Wrapper** — registers each artifact as a new version of your model in Unity Catalog, with a
   signature from your contract (or the MLflow model's own signature).
3. **Validator** — tests every new version **in its own environment**, built from its own
   requirements, using your sample input; optionally scores an evaluation dataset.
4. **Deployer** — creates or updates the serving endpoint (traffic split, compute, scale-to-zero,
   tags, usage policy, inference tables, permissions), then **calls the live endpoint**. If a real
   sample request fails, the endpoint is **rolled back** to what it was serving before and the
   deployment is marked Failed; otherwise the active version is marked `@champion` and the
   deployment is **Complete**.

Everything you deploy shows up on the **Deployed Models** board with a live status timeline, so you
can watch each step complete (or see exactly where it failed).

# 2. Accessing the app

Open the app URL in your browser (your administrator provides it — it looks like
`https://<app-name>-<id>.<region>.databricksapps.com`). You sign in with your Databricks
workspace credentials; the app shows your signed-in email in the header.

You do **not** need cluster, notebook, or CLI access to use the app — everything happens through
the web form. You do need permission to register models in the target Unity Catalog schema and to
create serving endpoints, and read access to the volume holding your artifact (your administrator
configures this).

# 3. The interface

The app has three tabs:

| Tab | What it's for |
|---|---|
| **Deployed Models** | Everything that has been (or is being) deployed, with live status, the lifecycle timeline, and actions (A/B test, new version). |
| **Deploy Model** | The form you fill in to start a new deployment. |
| **Saved drafts** | Deploy forms you saved with **Save draft** to finish later. Click one to resume it. |

# 4. Deploying a model — step by step

1. Go to the **Deploy Model** tab.
2. Fill in the form (each field is explained in section 5). Required fields have a red asterisk.
3. Click **Deploy**. (Not ready yet? Click **Save draft** and resume it from **Saved drafts**.)
4. You are taken to **Deployed Models**, where a new row appears **immediately** as
   *Deploy in progress* with a "submitted" event — no waiting for compute to warm up.
5. The row streams its lifecycle: **prepare → wrapper → validator → deployer**. When it
   finishes it turns green **Complete** and a **Serving → Open** link appears.

A typical CPU/SMALL deployment of a model file takes a few minutes (most of it is the serving
endpoint becoming ready, plus a minute or two to build the model's test environment). Large MLflow
model folders and code folders (e.g. PyTorch / transformers) take longer — 20+ minutes the first
time — because their environment is bigger.

# 5. Field reference

| Field | Required | What to enter |
|---|---|---|
| **Model Name** | Yes | A friendly name for the deployment (e.g. `house-price-regressor`). Shown on the board. |
| **Description** | No | Free text describing the model. |
| **Artifact** | Yes | **Where** it lives (UC Volume or S3) and the path. You don't say what it is (model file, MLflow model folder or code folder) — the deployment detects it; see section 6. For A/B tests you can add more than one, or reference an existing version. |
| **Model contract** | Yes | How the model's inputs/outputs are described (section 7): **Columnar schema**, **Sample input / output**, or **Use the model's own signature** (MLflow model folders). |
| **Input / Output schema** *(schema mode)* | — | JSON arrays of the model's columns (section 7). |
| **Sample input / output** *(sample mode)* | — | A real request/response example, e.g. `{"instances": [...]}` / `{"predictions": [...]}` (section 7). |
| **Sample input** *(model's own signature)* | No | A real request, used to test the model and the endpoint (section 7). |
| **Experiment name** | Yes | MLflow experiment path to log to. **Pre-filled** with the environment's configured experiment folder as `<folder>/<model name>`; edit only to override. |
| **Evaluation dataset** | No | A CSV/Parquet file to score the model against — **UC Volume** or **S3** (section 9). Leave blank to skip. |
| **UC Model name** | Yes | The Unity Catalog three-level name the model is registered under: **Catalog**, **Schema**, **Model**. |
| **Serverless usage policy** | Yes | A budget/usage policy ID for endpoint cost tracking. **Pre-filled** with the environment's configured policy; edit only to override. |
| **Compute** | Yes | CPU or GPU, size (SMALL / MEDIUM / LARGE), and scale-to-zero on/off (section 8). |
| **Tags** | No | A JSON object of extra tags for the endpoint (section 10). |
| **Endpoint permissions** | No | JSON granting others access to the endpoint (section 11). |

> **Note on Experiment and Serverless usage policy:** both are required, and both are pre-filled
> with the defaults your administrator configured for the environment. Change them only to override
> for a single deployment.

# 6. Choosing an artifact

Each deployment has one or more **variants**. A variant is either a **New artifact** (registered as
a new version) or an **Existing version** of the same Unity Catalog model, served as-is — used for
champion-vs-challenger A/B tests (section 14).

For a new artifact you choose **where** it lives and **what** it is, then enter the path.

## Where it lives

- **UC Volume** — `/Volumes/<catalog>/<schema>/<volume>/path/to/model.pkl` (or `…/model_folder/`).
- **S3 Bucket** — `s3://bucket/path/to/model.pkl`. The S3 location must be registered in Unity
  Catalog as an **external volume** you can read. The deployment swaps the S3 path for the matching
  `/Volumes/<catalog>/<schema>/<volume>/…` path and reads it like any UC Volume artifact; the
  timeline shows the swap, and a new version is pre-filled with the volume path. If no external
  volume covers the S3 path, the deployment fails with a clear message.

## What it is

| Format | What is at the path | How it is deployed |
|---|---|---|
| **Model file** | A single pickle / joblib file (e.g. scikit-learn, XGBoost, LightGBM). | Wrapped as an MLflow model using the contract you define (section 7). Tested in its own environment before serving. |
| **MLflow model folder** | A folder holding an exported MLflow model — it contains an `MLmodel` file, e.g. exported from **another workspace**. | Registered **as-is**, with its own code, artifacts, signature and environment. Tested in its own environment before serving. |
| **Code folder** | A folder with a `model.py`, the artifact files it loads, and optionally `requirements.txt` and a `code/` folder of helper modules. | Registered from your code (MLflow "models from code"). Tested in its own environment before serving. |

**Writing a code folder.** `model.py` defines an MLflow `PythonModel` and ends with
`mlflow.models.set_model(...)`. Every other file or folder in the code folder (except
`requirements.txt` and `code/`) is handed to your model as `context.artifacts["<name>"]`:

```python
import mlflow
from mlflow.pyfunc import PythonModel

class MyModel(PythonModel):
    def load_context(self, context):
        import joblib                                   # heavy imports go inside the methods
        self.model = joblib.load(context.artifacts["model.pkl"])

    def predict(self, context, model_input, params=None):
        return self.model.predict(model_input).tolist()

mlflow.models.set_model(MyModel())
```

- A **folder** artifact (e.g. `models/`) is passed as a directory path.
- Modules in `code/` can be imported by name inside `load_context` / `predict`.
- Keep heavy imports (torch, transformers, …) **inside** `load_context` / `predict`, not at the top
  of the file — the deployment reads `model.py` when registering it.
- List your packages, with versions, in `requirements.txt`; they are used to test and serve the model.

You don't choose the format — the deployment detects it from what is at the path: a single file is a
model file, a folder with `MLmodel` is an MLflow model folder, a folder with `model.py` is a code
folder. A folder with neither fails the deployment at the *prepare* step with a clear message, and
the detected format is shown on the deployment's timeline.

# 7. Defining the model contract

Every model needs a contract (a signature) so it can be registered and served — Unity Catalog
requires one. Pick a mode with the **Model contract** selector on the form.

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
back). Use this for text/NLP models, and remember a model file must be a **complete pipeline** (e.g.
vectorizer + classifier) that actually accepts that input.

Because the sample is a real request, it is also used to **test the live endpoint** — if the
endpoint can't answer it, the deployment is rolled back (section 16).

## Mode C — Use the model's own signature (MLflow model folders)

Available when **every** new variant is an MLflow model folder. No schema or sample output is
needed — the model's own signature is used. You can still paste an optional **Sample input**, e.g.
`{"inputs": ["Pregnancy Test", "EKG"]}`; it is used to test the model in its own environment and to
test the endpoint. Leave it blank to use the model's saved input example, if it has one.

If an MLflow model folder has **no** signature (older exports can lack one), use Mode A or B — the
signature you enter is added to it.

# 8. Compute options

| Option | Choices | Notes |
|---|---|---|
| **Compute type** | CPU or GPU | GPU requires serverless GPU serving quota; provisions slower and costs more. |
| **GPU type** | e.g. T4, A10, H100 | Only when Compute type = GPU. |
| **Size** | SMALL / MEDIUM / LARGE | Pick based on model size and expected load. Larger models (e.g. BERT-based) need MEDIUM or more. |
| **Scale-to-zero** | On / Off | On = the endpoint sleeps when idle (cheaper; first call after idle has a cold-start delay). Off = always warm. |

# 9. Evaluation dataset (optional)

If you provide an **Evaluation dataset** (a CSV or Parquet file), the validator scores the model
against it and logs `mlflow.evaluate` metrics. Like an artifact, choose where it lives:

- **UC Volume**: `/Volumes/<catalog>/<schema>/<volume>/path/to/eval.csv`
- **S3**: `s3://bucket/path/to/eval.parquet` — the S3 location must be covered by a Unity Catalog
  **external volume** you can read. The deployment swaps it for the matching `/Volumes/…` path
  (shown on the timeline); if no external volume covers it, the deployment fails with a clear
  message.

The file's columns must be the model's features **plus a target column named exactly like your
(first) output-schema field** (e.g. `price`, `prediction`, `churn`).

- If the output field is `long`/integer → **classifier** metrics are computed.
- Otherwise → **regressor** metrics.
- If the target column is missing, the rows are just scored (no metrics).
- The dataset is scored in the model's own environment (the same one used for testing).

Evaluation is **non-fatal**: a bad eval file logs a warning and the deployment still completes.

# 10. Tags

The **Tags** field is a JSON object of extra key/value tags applied to the serving endpoint:
```json
{ "team": "ds-housing", "project": "pricing" }
```
Your environment also applies a standard set of governance/chargeback tags automatically (e.g.
application, cost center, team, environment). Any tag you enter here is added on top, and **your
value wins** if a key overlaps with a standard tag. When you deploy a new version, the endpoint's
tags are re-synced to the form: a tag you removed from the form is removed from the endpoint.

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
- You keep CAN_MANAGE unless you list yourself under a different level, which is then honored.

# 12. Monitoring a deployment

On the **Deployed Models** board each row shows:

- **Model name** (click it to deploy a new version — see section 13), **UC name**, **Version**,
  **Deploy Date** (full date and time), **Status**, and **Serving** actions.
- A **status badge**: amber **Deploy in progress**, green **Complete**, or red **Failed**.
- A **collapsible lifecycle timeline** (click the chevron). While in progress it shows a live
  indicator and advances through the steps, each with a status and timestamp:
  *prepare → wrapper → validator → deployer → complete.* Notes along the way tell you
  what happened — e.g. an S3 path swapped for its volume path, the detected format of each variant,
  the model's prediction in its own environment, or a rollback.

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
3. Change the **artifact** to the new file or folder (and any other settings you want).
4. Click **Deploy new version**.

The model gets a new registered version and the **same endpoint is updated in place** (no
downtime). `@champion` moves to the new version **after the endpoint test passes**; if the endpoint test
fails, the endpoint goes back to the previous version and `@champion` stays where it was.

# 14. A/B testing

You can serve two variants of the same model on one endpoint with a traffic split.

**Two new artifacts:**

1. On the Deploy form, click **Add artifact (A/B variant)** so there are two variants.
2. Set each variant's **Traffic %** — the footer must read **Traffic total: 100%** before you can
   deploy.
3. Deploy. Both variants are registered and served on one endpoint at the split you chose; each
   variant is tested separately; `@champion` goes to the higher-traffic variant.

**Champion vs. challenger (existing version + new artifact):**

1. On a **Complete** row, click the **A/B test** button.
2. Variant A is the **Existing version** (pick the current champion from the dropdown); variant B
   is a **New artifact** (the challenger). Set the traffic split.
3. Deploy. The existing version is served as-is (not re-registered); only the challenger is
   registered as a new version.

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

**MLflow model folder** — use the request shape the model expects (its sample input), e.g.:
```
databricks serving-endpoints query <endpoint_name> \
  --json '{"inputs": ["Liver biopsy", "Pregnancy Test"]}' \
  --profile <your-profile>
# → {"predictions": ["invasive", "non-invasive"]}
```

The endpoint name defaults to `<model>_endpoint`.

# 16. When a deployment fails

If a deployment turns **Failed**, expand the row's lifecycle timeline — the last event is a red
**FAILED** entry with the step that failed and the error message:

| Step that failed | Common cause |
|---|---|
| **Prepare** | The path doesn't exist; an S3 path isn't covered by an external volume you can read; the path is a folder with no `model.py` or `MLmodel`; "Use the model's own signature" was chosen but an artifact isn't an MLflow model folder; or no signature is available (choose a Schema or Sample contract). |
| **Wrapper** | The file isn't a loadable model (wrong path, not a pickle/joblib model, unpickling error), or the model couldn't be registered in Unity Catalog. |
| **Validator** | The model couldn't load or predict on your sample input **in its own environment** (e.g. the sample doesn't match the model, a missing package in its requirements, or a bug in `model.py`). |
| **Deployer** | The serving endpoint could not be created/updated (permissions, quota, or configuration) — or the live endpoint couldn't answer your **sample input**, in which case it is **rolled back** to its previous version (the timeline shows *rollback*), so callers keep getting the old model. |

Notes:

- When you gave **no sample input**, the tests use a dummy row built from your schema. If the model
  can't handle that dummy row, it's noted as a warning and the deployment continues. A **real
  sample input** that fails always fails the deployment.
- If the endpoint test fails on a **brand-new** endpoint, there's nothing to roll back to — the endpoint
  stays in place, and the deployment is marked Failed.

Fix the underlying issue and deploy again.

# 17. Tips

- **Match the input schema to the model exactly** (schema mode) — the most common failure is
  declaring the wrong number or order of features.
- For **text / JSON / tensor** models, use **Sample input / output** mode instead of a schema, and
  make sure the artifact is a **complete pipeline** (e.g. vectorizer + classifier) that accepts the
  sample you paste.
- Give a **real sample input** whenever you can — it is the strongest test, both in the model's own
  environment and against the live endpoint.
- For an **MLflow model folder**, choose **Use the model's own signature** and paste a sample input.
- For a **code folder**, pin package versions in `requirements.txt` and keep heavy imports inside
  `load_context` / `predict`.
- Use **`double`** for continuous features and regression outputs; use **`long`** for class labels
  so the validator scores it as a classifier.
- Keep the pre-filled **Experiment** and **Serverless usage policy** unless you specifically need to
  override the environment defaults.
- For A/B tests, remember the traffic must total **100%** before you can deploy.
- The first call to a **scale-to-zero** endpoint after it's been idle has a short cold-start delay;
  turn scale-to-zero **off** for latency-sensitive workloads.
