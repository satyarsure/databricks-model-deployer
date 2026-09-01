# Manual testing guide — Model Deployer

A set of end-to-end test cases you can run against the deployed app. They exercise the happy
path, an A/B traffic split, and two failure paths — every stage of
**wrapper → validator → deployer** and the lifecycle timeline in the UI.

## 0. Prerequisites (one-time)

1. **Build the fixtures.** Import `testing/setup_test_artifacts.py` as a notebook and run it
   (serverless is fine), passing your `catalog` / `schema` / `volume` as parameters. It pins
   `scikit-learn` / `numpy` / `pandas` / `joblib` to the same versions as the deploy job's
   `deploy-job/requirements.txt`, so the pickles load cleanly downstream. It writes these files to
   `/Volumes/<catalog>/<schema>/<volume>/test_models/`:

   | file | model | features → output |
   |---|---|---|
   | `house_price_linreg.pkl` | LinearRegression | sqft, bedrooms, bathrooms, age → price |
   | `house_price_linreg_v2.pkl` | LinearRegression (v2) | same (for the new-version test) |
   | `energy_gbr.pkl` | GradientBoostingRegressor | temperature, humidity, occupancy → energy_kwh |
   | `iris_rf.pkl` | RandomForestClassifier | sepal/petal ×4 → class 0/1/2 |
   | `churn_logreg.pkl` | LogisticRegression | tenure, monthly_charges, total_charges → 0/1 |
   | `churn_rf.pkl` | RandomForestClassifier | same 3 features → 0/1 |
   | `credit_risk_gbc.pkl` | GradientBoostingClassifier | income, age, loan_amount, credit_score → 0/1/2 |
   | `house_price_eval.csv` | eval dataset | includes the `price` target column |
   | `energy_eval.csv` | eval dataset | includes the `energy_kwh` target column |
   | `not_a_model.txt` | (not a model) | used by the wrapper-failure test |

2. **Fill in your environment values** (used throughout the cases below):

   | placeholder | your value |
   |---|---|
   | `<catalog>` | e.g. `main` |
   | `<schema>` | e.g. `default` |
   | `<volume>` | e.g. `artifacts` |
   | `<experiment>` | e.g. `/Users/you@example.com/experiments/model_deployer_tests` |
   | `<budget-policy-id>` | a serverless usage policy (budget policy) UUID |

   Artifact paths below are written as `/Volumes/<catalog>/<schema>/<volume>/test_models/<file>`.

3. Open the app, go to the **Deploy Model** tab. Each test = fill the form → **Save & Deploy** →
   watch the row on **Deployed Models** (it auto-expands and streams the lifecycle timeline while
   in progress).

> **Input / Output schema** are entered as **JSON strings** — paste the JSON blocks verbatim.
> **Tags** is a JSON object. Every feature type is `double`; classifier outputs use `long`.

---

## TC1 — Simple regressor (happy path, CPU/SMALL, scale-to-zero ON)

Baseline sanity check: one artifact, minimal compute, no eval dataset.

| field | value |
|---|---|
| Model Name | `house-price-regressor` |
| Description | `Linear regression predicting home price` |
| Artifact | UC Volume · `/Volumes/<catalog>/<schema>/<volume>/test_models/house_price_linreg.pkl` |
| Experiment name | `<experiment>` |
| Evaluation dataset | *(leave blank)* |
| UC Model name | Catalog `<catalog>` · Schema `<schema>` · Model `house_price_model` |
| Serverless usage policy | `<budget-policy-id>` |
| Compute | CPU · SMALL · Scale-to-zero **ON** |

Input schema:
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
Tags:
```json
{ "team": "ds-housing", "env": "test" }
```

**Expected:** lifecycle runs wrapper → validator → deployer → **Complete**; endpoint
`house_price_model_endpoint` becomes READY; `@champion` alias set on v1.

**Query it:**
```bash
databricks serving-endpoints query house_price_model_endpoint \
  --json '{"dataframe_records":[{"sqft":2200,"bedrooms":3,"bathrooms":2,"age":8}]}' \
  --profile <PROFILE>
# ≈ 382000
```

---

## TC2 — Regressor with eval dataset + tags (CPU/MEDIUM, scale-to-zero OFF)

Exercises `mlflow.evaluate` (the eval CSV's target column matches the output field name) and
chargeback tags; larger compute, always-on.

| field | value |
|---|---|
| Model Name | `energy-forecaster` |
| Description | `Gradient-boosted energy consumption forecaster` |
| Artifact | UC Volume · `/Volumes/<catalog>/<schema>/<volume>/test_models/energy_gbr.pkl` |
| Experiment name | `<experiment>` |
| Evaluation dataset | `/Volumes/<catalog>/<schema>/<volume>/test_models/energy_eval.csv` |
| UC Model name | `<catalog>` · `<schema>` · `energy_forecaster` |
| Serverless usage policy | `<budget-policy-id>` |
| Compute | CPU · MEDIUM · Scale-to-zero **OFF** |

Input schema:
```json
[
  { "name": "temperature", "type": "double" },
  { "name": "humidity", "type": "double" },
  { "name": "occupancy", "type": "double" }
]
```
Output schema:
```json
[ { "name": "energy_kwh", "type": "double" } ]
```
Tags:
```json
{ "team": "facilities", "cost_center": "cc-4417", "env": "test" }
```

**Expected:** validator stage logs `mlflow.evaluate complete`; **Complete**; endpoint
`energy_forecaster_endpoint` READY.

**Query it:**
```bash
databricks serving-endpoints query energy_forecaster_endpoint \
  --json '{"dataframe_records":[{"temperature":25,"humidity":60,"occupancy":20}]}' \
  --profile <PROFILE>
# ≈ 122 (kWh)
```

---

## TC3 — Multiclass classifier (RandomForest, CPU/SMALL)

Classifier with an integer (`long`) output.

| field | value |
|---|---|
| Model Name | `iris-classifier` |
| Description | `Iris species random forest` |
| Artifact | UC Volume · `/Volumes/<catalog>/<schema>/<volume>/test_models/iris_rf.pkl` |
| Experiment name | `<experiment>` |
| UC Model name | `<catalog>` · `<schema>` · `iris_classifier` |
| Serverless usage policy | `<budget-policy-id>` |
| Compute | CPU · SMALL · Scale-to-zero ON |

Input schema:
```json
[
  { "name": "sepal_length", "type": "double" },
  { "name": "sepal_width", "type": "double" },
  { "name": "petal_length", "type": "double" },
  { "name": "petal_width", "type": "double" }
]
```
Output schema:
```json
[ { "name": "prediction", "type": "long" } ]
```
Tags:
```json
{ "team": "ml-research" }
```

**Expected:** **Complete**; `iris_classifier_endpoint` READY.

**Query it:**
```bash
databricks serving-endpoints query iris_classifier_endpoint \
  --json '{"dataframe_records":[
    {"sepal_length":5.1,"sepal_width":3.5,"petal_length":1.4,"petal_width":0.2},
    {"sepal_length":6.7,"sepal_width":3.0,"petal_length":5.2,"petal_width":2.3}
  ]}' --profile <PROFILE>
# → [0, 2]  (setosa, virginica)
```

---

## TC4 — A/B traffic split 70 / 30 ⭐

Two variants of the **same** UC model on one endpoint, split 70/30. `@champion` goes to the
higher-traffic variant (A).

On the form: **Add artifact (A/B variant)** so there are two, and set the traffic numbers. The
footer must read **Traffic total: 100%** before Save is allowed.

| field | value |
|---|---|
| Model Name | `churn-predictor` |
| Description | `A/B: logistic regression vs random forest churn` |
| Variant **A** | UC Volume · `.../test_models/churn_logreg.pkl` · Traffic **70** |
| Variant **B** | UC Volume · `.../test_models/churn_rf.pkl` · Traffic **30** |
| Experiment name | `<experiment>` |
| UC Model name | `<catalog>` · `<schema>` · `churn_predictor` |
| Serverless usage policy | `<budget-policy-id>` |
| Compute | CPU · SMALL · Scale-to-zero ON |

Input schema:
```json
[
  { "name": "tenure", "type": "double" },
  { "name": "monthly_charges", "type": "double" },
  { "name": "total_charges", "type": "double" }
]
```
Output schema:
```json
[ { "name": "churn", "type": "long" } ]
```
Tags:
```json
{ "team": "growth", "experiment": "churn-ab" }
```

**Expected:** two model versions registered (e.g. `A:1,B:2`); endpoint `churn_predictor_endpoint`
has **two served entities** with a **70/30** traffic split; **Complete**.

**Verify the split:**
```bash
databricks serving-endpoints get churn_predictor_endpoint --profile <PROFILE> \
  | jq '.config.traffic_config.routes'
# two routes: 70 and 30

databricks serving-endpoints query churn_predictor_endpoint \
  --json '{"dataframe_records":[{"tenure":3,"monthly_charges":110,"total_charges":330}]}' \
  --profile <PROFILE>
# → 1 (likely to churn);  a long-tenure/low-charge row → 0
```

---

## TC5 — Validation FAILURE (schema mismatch) ⭐

Deliberately declare **2** input features for a model trained on **4**. The pipeline fails the
model validation — sklearn raises *"X has 2 features, but LinearRegression is expecting 4
features as input"*. The row goes **Failed**, and the lifecycle timeline shows the failing stage
with the error message.

| field | value |
|---|---|
| Model Name | `bad-schema-test` |
| Artifact | UC Volume · `.../test_models/house_price_linreg.pkl` |
| Experiment name | `<experiment>` |
| UC Model name | `<catalog>` · `<schema>` · `house_price_badschema` |
| Serverless usage policy | `<budget-policy-id>` |
| Compute | CPU · SMALL |

Input schema (intentionally wrong — only 2 of 4 features):
```json
[
  { "name": "sqft", "type": "double" },
  { "name": "bedrooms", "type": "double" }
]
```
Output schema:
```json
[ { "name": "price", "type": "double" } ]
```

**Expected:** status **Failed**; no endpoint created; the Deployed Models row shows the error, and
the expanded lifecycle timeline ends on a red **FAILED** event carrying the sklearn feature-count
message.

---

## TC6 — Wrapper FAILURE (artifact won't load) ⭐

Point at a file that isn't a model. The **wrapper** stage fails to load the artifact.

| field | value |
|---|---|
| Model Name | `broken-artifact-test` |
| Artifact | UC Volume · `.../test_models/not_a_model.txt` |
| Experiment name | `<experiment>` |
| UC Model name | `<catalog>` · `<schema>` · `broken_artifact` |
| Serverless usage policy | `<budget-policy-id>` |
| Compute | CPU · SMALL |

Input schema: `[]`  ·  Output schema: `[ { "name": "prediction", "type": "double" } ]`

**Expected:** status **Failed** at the **wrapper** stage (unpickling error). *(A nonexistent path
such as `.../test_models/does_not_exist.pkl` fails the same way — a good second variant.)*

---

## TC7 — Deploy a NEW VERSION (versioning + champion move) ⭐

Tests the model-name hyperlink → prefilled form. Requires **TC1** to have completed.

1. On **Deployed Models**, click the **`house-price-regressor`** name.
2. The form opens prefilled; **Model name, Experiment, and UC model are locked** (greyed out).
3. Change **only the artifact** to the v2 file:
   `/Volumes/<catalog>/<schema>/<volume>/test_models/house_price_linreg_v2.pkl`
4. **Deploy new version**.

**Expected:** a new registered version (v2) of `house_price_model`; the same
`house_price_model_endpoint` is **updated in place** (config update, not recreated); `@champion`
moves to v2. Re-run the TC1 query — the endpoint still answers.

```bash
databricks serving-endpoints get house_price_model_endpoint --profile <PROFILE> \
  | jq '{ready: .state.ready, config_update: .state.config_update}'
# ready == "READY" AND config_update == "NOT_UPDATING" when the swap is done
```

---

## TC8 — Multiclass classifier, LARGE compute + chargeback tags

Bigger compute, always-on, richer tag set (chargeback).

| field | value |
|---|---|
| Model Name | `credit-risk-classifier` |
| Description | `Gradient-boosted 3-class credit risk` |
| Artifact | UC Volume · `.../test_models/credit_risk_gbc.pkl` |
| Experiment name | `<experiment>` |
| UC Model name | `<catalog>` · `<schema>` · `credit_risk` |
| Serverless usage policy | `<budget-policy-id>` |
| Compute | CPU · LARGE · Scale-to-zero **OFF** |

Input schema:
```json
[
  { "name": "income", "type": "double" },
  { "name": "age", "type": "double" },
  { "name": "loan_amount", "type": "double" },
  { "name": "credit_score", "type": "double" }
]
```
Output schema:
```json
[ { "name": "risk_class", "type": "long" } ]
```
Tags:
```json
{ "team": "risk", "cost_center": "cc-9001", "pii": "false", "env": "test" }
```

**Expected:** **Complete**; `credit_risk_endpoint` READY; endpoint tags include the chargeback
values; endpoint budget policy = your `<budget-policy-id>` (visible under the endpoint's details).

**Query it:**
```bash
databricks serving-endpoints query credit_risk_endpoint \
  --json '{"dataframe_records":[
    {"income":150000,"age":45,"loan_amount":10000,"credit_score":800},
    {"income":30000,"age":25,"loan_amount":90000,"credit_score":400}
  ]}' --profile <PROFILE>
# → [2, 0]  (low risk, high risk)
```

---

## (Optional) TC9 — GPU compute

Same as TC1 but set **Compute = GPU**, GPU Type = `T4` (or A10/H100). Only run this if your
workspace has serverless GPU serving quota — GPU endpoints take longer to provision and cost more.
The `gpu_type` is recorded as an endpoint tag; the GPU tier is encoded by the workload type.

---

## What to check in the UI (every case)

- **Deployed Models** row appears immediately as *Deploy in progress* and **auto-expands**.
- The **lifecycle timeline** streams live (amber "live" dot), advancing through
  **wrapper → validator → deployer**, each with a status + timestamp.
- On success the **Status** badge turns green **Complete** and the **Serving → Open** link works.
- On failure (TC5/TC6) the badge turns red **Failed**, the error shows under the badge, and the
  timeline's last event is a red **FAILED** with the message.
- **Search** box filters by model name / UC name (contains).

## Cleanup (optional)

```bash
# delete test endpoints
for e in house_price_model_endpoint energy_forecaster_endpoint iris_classifier_endpoint \
         churn_predictor_endpoint credit_risk_endpoint; do
  databricks serving-endpoints delete "$e" --profile <PROFILE>
done
# registered models / rows in <catalog>.<schema>.model_deployments and
# .model_lifecycle_events can be dropped via SQL if you want a clean slate.
```
