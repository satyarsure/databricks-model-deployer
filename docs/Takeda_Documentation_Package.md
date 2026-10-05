# Model Deployer — Documentation Package (DRAFT for Takeda review)

**Status:** Draft for review. Please validate against Takeda's internal processes and flag
anything that should change. Placeholders marked `<...>` need Takeda-specific values.

Covers:
1. Deployment & Execution Flow
2. System Configuration & Process Use
3. Monitoring & Operations
4. Access Requests & Onboarding

---

## 1. Deployment & Execution Flow

The **Model Deployer** is a Databricks App that takes a model artifact, registers it to
Unity Catalog, serves it on a Model Serving endpoint, and (optionally) runs A/B variants —
all from a UI, so model owners don't script deployments by hand.

### End-to-end: deploy a model through the app
1. **Put the model artifact in a UC Volume.** In Catalog Explorer, open (or create) a
   Volume and **Upload** the model. Supported artifact types:
   - A **single model file** (e.g. a scikit-learn `.pkl`) — the app wraps it in an MLflow pyfunc.
   - A **full MLflow model folder** (contains an `MLmodel` file) — registered **as-is**,
     keeping its own code, artifacts, and dependencies (use this for pipelines, e.g. a
     text model that bundles a transformer + classifier).
   *(S3 is also supported — see Configuration — but UC Volume is the tested path.)*
2. **Open the Model Deployer app → Deploy Model.**
3. **Fill the form:**
   - **Model Name** + description.
   - **Artifacts:** source = UC Volume, **path = `/Volumes/<catalog>/<schema>/<volume>/<folder>`**
     (must start with `/Volumes/...`). Add more variants for an A/B test (traffic must total 100%).
   - **Model contract** (required — Unity Catalog needs a signature):
     - *Sample input / output* (recommended for text/JSON models): paste a real request and
       response, e.g. input `["Pregnancy Test", "EKG"]`, output `["non-invasive", "non-invasive"]`.
     - *or Input/Output schema*: column names + types.
   - **UC Model name:** `<catalog>.<schema>.<model>` (3-level) — where it registers.
   - **Experiment** (required; pre-filled), **Serverless usage policy** (pre-filled),
     **Tags** (governance/chargeback), **Endpoint permissions** (optional), **Compute**
     (CPU/GPU, size, scale-to-zero).
4. **Deploy** (or **Save draft** to finish later). This triggers the **deploy job**, which runs:
   - **Wrapper** → registers the model to UC (as-is for an MLflow folder; wrapped for a single file).
   - **Validator** → smoke-tests the model; non-fatal if the job env lacks the model's packages
     (it's validated on the serving endpoint instead).
   - **Deployer** → creates/updates the serving endpoint (traffic split, compute, scale-to-zero, tags).
5. **Wait for the endpoint to be Ready** (first build can take 10–25 min for heavy models).
6. **Query** it (endpoint → *Query endpoint*): `{"inputs": [...]}` → `{"predictions": [...]}`.

### Worked example (Protocol-Intelligence model)
A real deployment done through the app in R&D-dev:
- **Artifact:** `/Volumes/usdev_rnd_non_gxp/rnd_us_mart_po/model_deployer_artifacts/pi-model`
  (a full MLflow model folder — ClinicalBERT + SVM — registered as-is)
- **Contract (sample):** input `["Pregnancy Test", "EKG"]` → output `["non-invasive", "non-invasive"]`
- **UC model:** `usdev_rnd_non_gxp.rnd_us_mart_po.protocol_intelligence_ui`
- **Compute:** CPU, Small, scale-to-zero
- **Result:** endpoint `protocol_intelligence_ui_endpoint` → querying `{"inputs": ["Pregnancy Test", "EKG"]}`
  returns `{"predictions": ["non-invasive", "non-invasive"]}`, matching the existing prod endpoint.

### A/B testing (champion/challenger)
Add a second artifact variant and set traffic (e.g. 70/30). A variant can also point at an
existing registered version. At most 5 concurrent deployments run at once; the rest queue.

---

## 2. System Configuration & Process Use

### What the app is made of
Deployed as **Databricks Asset Bundles (DABs)** from the Git repo
(`databricks-model-deployer`):
- **App** (`app/`) — the Web UI (Databricks App).
- **Deploy job** (`deploy-job/`) — the register → validate → serve pipeline (`deploy_model` notebook).
- **Lakebase Postgres** — the app's status/lifecycle store.
- **(Optional) Governance bundle** (`governance/`) — owns the UC schema + artifacts Volume.

### How code becomes the running app (deployment of the tool)
The repo is **not** linked live to the workspace. A maintainer runs:
```
databricks bundle deploy -t <dev|qa|prod>
```
which uploads the code and creates/updates the app + job in the target workspace.
Per-environment values (workspace path, catalog/schema, policy, Lakebase, tags) live in a
gitignored `values.local.yml` (see `*/values.local.example.yml`). CI/CD workflows that run
`bundle validate` / `bundle deploy` are in `.github/workflows/`.

### Environments & promotion
Targets **dev → qa (Test) → prod**. Two things are promoted, separately, both through GitHub
Actions with an approval on each environment (Takeda change control):
- **The tool** — a standard code promotion: `bundle-deploy.yml` (bundle deploy to the higher target).
- **Each model** — `promote-model.yml`: the registered version that passed in the lower
  environment is **copied unchanged** into the next environment's catalog and served there. The run
  **fails unless** the new endpoint returns the expected answer, and its summary is the evidence for
  the change ticket. Per-model specs live in `promotions/`. The same copy is available in the app
  as the **UC model (promote)** artifact source.

Keep **Unity Catalog as the system of record** for models (UC enforces signatures). Setup:
`docs/Test_Prod_Promotion_Setup.md`. Prod change-request content: `docs/Production_Change_Request_PO.md`.

### Identity
The app runs with an **app service principal** plus **on-behalf-of (OBO)** user auth, so each
user acts with their own permissions. The deploy job runs as its configured identity.

### Key config fields (set per environment)
`root_path`, `catalog`, `schema`, `experiment`, `budget_policy_id`, `resource_tags`,
Lakebase (`lakebase_project`, `pg_host`, …), `app_sp`, `max_concurrent_deployments`,
and (for cross-workspace import) `source_registry_secret_scope`.

---

## 3. Monitoring & Operations

### Endpoint health & metrics
- **Serving UI → the endpoint → Metrics**: latency, request rate, errors, scale events.
- **State**: *Ready* = serving; *Updating* = building. First build of a heavy model is slow.

### Usage / inference logging
- **AI Gateway** on the endpoint: enable **usage tracking** and **inference tables** to log
  requests/responses to a UC table for audit and analysis.

### Lakehouse Monitoring (drift / features / accuracy)
- Point **Lakehouse Monitoring** at the inference table to track data/prediction drift and,
  where ground truth is available, accuracy over time. *(Set up per model; not enabled by
  default — a recommended next step.)*

### Routine tasks & triage
- **Deploy fails at the job**: open **Jobs & Pipelines → `mlops_deploy_model_job` → Runs →**
  the failed run → task **Output/Logs**.
- **Endpoint build fails**: endpoint → **Logs → Build logs**. Common causes:
  - *Old pinned versions not in the serving repo* (e.g. `scipy==1.10.1 not found`) → use
    installable/current version pins in the model's `requirements.txt`.
  - *Heavy model needs internet* → bundle weights (e.g. ClinicalBERT) **into** the artifact;
    serving has no internet.
- **"Failed to trigger deploy job: Bad Request"** → usually transient; retry. If persistent,
  check for a stuck/queued run (concurrency limit).
- See `docs/Operations_Runbook.md` §11 for deploying deep-learning/heavy models.

---

## 4. Access Requests & Onboarding

To use the Model Deployer and avoid delays, request these up front (via **Pratusha or
Shyam**; cc the project lead, **Alain**):

| Need | Access to request |
|---|---|
| Open & use the app | Access to the **Model Deployer app** in the target workspace |
| Register models | **Unity Catalog** privileges on the target catalog/schema: `USE CATALOG`, `USE SCHEMA`, `CREATE MODEL` |
| Read model artifacts | `READ VOLUME` on the artifacts **UC Volume** (and `WRITE VOLUME` to upload) |
| Serve / query | Permission to **create/query serving endpoints** (or endpoint-level `CAN_QUERY`/`CAN_MANAGE`) |
| Run/trigger the deploy job | Access to `mlops_deploy_model_job` (CAN_MANAGE_RUN) |
| Deploy the tool itself | **Databricks CLI** access + repo access (`satyarsure/databricks-model-deployer`) for `bundle deploy` |
| Compute | A cluster / serverless entitlement to run notebooks |

### Onboarding checklist for a new user
1. Confirm workspace login (SSO).
2. Request the UC + Volume + app + endpoint access above.
3. Verify access by opening the app and the target catalog/schema in Catalog Explorer.
4. Capture the **exact error text** on any permission failure and send it to the admin.

---

*Draft generated to accelerate review — please mark corrections so it matches Takeda's
internal process, naming, and governance requirements.*
