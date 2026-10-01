# Protocol-Intelligence Model — Migration & Deploy Runbook

Goal (2026-10-15 go/no-go): stand up the **real** Protocol-Intelligence model in the
R&D-dev workspace through the Model Deployer app, and query it for correct predictions.

## What model, and why not the pickle

The standalone `svm_model_linear.pkl` **deploys but predicts wrong** — the real model is a
full pipeline: abbreviation expansion (`mapping_dictionary.json`) → vectorizer → SVM (0/1)
→ label decode to `invasive`/`non-invasive`. That whole pipeline already exists as a
**registered MLflow model** behind the working endpoint `Protocol-Intelligence-AI-1`.
Satya's analysis: **serve the complete registered model as-is** — keep its code, artifacts,
and pinned requirements; don't re-wrap it.

## Chosen path: export → UC Volume → deploy "as-is"

We use [`mlflow-export-import`](https://github.com/mlflow/mlflow-export-import) to export the
registered model as a portable folder, land it in a **UC Volume** in R&D-dev, then deploy it
through the app's **UC-Volume MLflow-folder** source, which registers it to UC *as-is*
(`deploy-job/src/notebooks/deploy_model.py` — `_find_mlmodel_dir` / register-as-is path).

**Why this path over the cross-workspace registry source:** the app's "Workspace registry"
source needs an admin to wire `source_registry_secret_scope` (source-workspace host+token
secrets) — a cross-workspace credential blocker. The export→Volume route needs none of that,
and it splits cleanly across access levels:

```
Satya (CLI on source workspace)          You (R&D-dev, UI only, Island browser)
────────────────────────────────        ──────────────────────────────────────
export-model  ──►  model folder  ──►  upload to UC Volume  ──►  Model Deployer app
                                                                (source = Volume folder,
                                                                 register as-is → serve → query)
```

---

## Step A — Export the registered model (Satya, source workspace CLI)

```bash
pip install git+https://github.com/mlflow/mlflow-export-import/#egg=mlflow-export-import

# Point at the SOURCE workspace (where Protocol-Intelligence-AI-1's model is registered)
export MLFLOW_TRACKING_URI=databricks
export DATABRICKS_HOST=https://<source-workspace-host>
export DATABRICKS_TOKEN=<source-token>

export-model \
  --model <source_registered_model_name> \
  --output-dir ./protocol-intelligence-export \
  --versions <the_version_serving_AI-1>          # or --stages Production
```

This writes the model version(s) with their `MLmodel`, `requirements.txt`/`conda.yaml`,
`artifacts/` (incl. `mapping_dictionary.json`), and any `code/` — everything needed to serve.

## Step B — Land the export in a UC Volume (R&D-dev)

Put the export folder into a UC Volume the deploy job can read, e.g.
`/Volumes/<catalog>/<schema>/artifacts/protocol-intelligence/`. Options:
- Databricks UI: Catalog → the Volume → **Upload** the export folder, **or**
- `databricks fs cp -r ./protocol-intelligence-export dbfs:/Volumes/<catalog>/<schema>/artifacts/protocol-intelligence` (whoever has CLI to R&D-dev).

The folder that contains the `MLmodel` file is what the app points at.

## Step C — Dependency preflight (do this the moment the export lands) ⚠️

This is the **known top risk**: old Python/package pins may not build on Model Serving.
Before deploying, open the export's `MLmodel` + `requirements.txt`/`conda.yaml` and check:

- [ ] **Python version** — Model Serving supports a limited set; a very old `python_version`
      in `MLmodel` may need bumping to the nearest supported minor.
- [ ] **`mlflow` version pin** — must be installable and compatible with the serving runtime.
- [ ] **scikit-learn / numpy / scipy pins** — exact old pins can fail to build wheels; note any
      that have no wheel for the supported Python.
- [ ] **Private/internal packages** — anything not on public PyPI must be vendored into the
      model's `code/` or the requirements adjusted.
- [ ] **Artifacts present** — `mapping_dictionary.json` and the vectorizer/label files are in
      the export (the pipeline is wrong without them).

Record any pin that must change; adjusting requirements is expected for old models.

## Step D — Deploy through the Model Deployer app (You, UI)

In the app, create a deployment with:
- **Source:** UC Volume — path to the folder holding `MLmodel` (Step B). Registered **as-is**.
- **Target UC model:** `<catalog>.<schema>.<model_name>` (3-level).
- **Signature / contract:** input `instances: array<string>`, output the business label.
- **Endpoint:** scale-to-zero as appropriate; apply the serverless budget policy.

## Step E — Validate (the go/no-go check)

Query the new endpoint with the agreed contract and confirm business labels:

```json
{"instances": ["Pregnancy Test", "EKG"]}
```

Expected: correct `invasive` / `non-invasive` labels, matching `Protocol-Intelligence-AI-1`.
Spot-check a few known inputs against the existing endpoint.

---

## Alternative (only if secrets get wired): native Workspace-registry source

If an admin sets the deploy job's `source_registry_secret_scope` with the source workspace's
`<prefix>-host` / `<prefix>-token` secrets (INSTALL.md, "Import from a workspace model
registry"), the app's **Workspace registry** source can pull the version directly — no manual
export. Same as-is registration; skips Steps A–B. Use whichever unblocks first.

## Who does what

| Step | Owner | Access needed |
|---|---|---|
| A export | Satya | CLI on source workspace |
| B upload to Volume | Satya / whoever has R&D-dev CLI or UI upload | Volume write |
| C preflight | You + me (I read the files) | the export folder |
| D deploy | You | Model Deployer app UI |
| E validate | You | app UI + endpoint query |
