# Takeda Technical Team — Knowledge Transfer & Enablement

Covers Milestone 1.3 "Takeda Technical Team Knowledge Transfer & Enablement".
This is the **agenda + materials** for a live KT session; the session itself is a meeting.

## Audience & goal
Takeda engineers/ops who will **own and operate** the Model Deployer after handover.
Goal: by the end, a Takeda engineer can deploy a model through the app, promote it, and
triage a failure — without us in the room.

## Pre-reads (send 2 days before)
- `docs/Takeda_Documentation_Package.md` (the 4-section package)
- `docs/Operations_Runbook.md` (ops/triage)
- `docs/Model_Deployer_User_Guide.md` (end-user deploy)
- `docs/Test_Prod_Promotion_Setup.md` (environments)

## Agenda (90 min)
| Time | Topic | Who |
|---|---|---|
| 0:00–0:10 | Architecture overview — app, deploy job, UC, serving, Lakebase | us |
| 0:10–0:30 | **Live demo:** deploy a model through the app UI end-to-end | us |
| 0:30–0:45 | The deploy job internals — wrapper → validator → deployer; where logs live | us |
| 0:45–1:00 | Heavy/deep-learning models — bundling weights, pins, the two fixes we made | us |
| 1:00–1:15 | Promotion dev→test→prod, bundles, CI/CD, change control | us |
| 1:15–1:30 | Monitoring (endpoint metrics, inference tables, Lakehouse Monitoring) + triage drills | us + Takeda |

## Live demo script (the core 20 min)
1. Show the **Protocol-Intelligence** artifact in the UC Volume
   (`/Volumes/usdev_rnd_non_gxp/rnd_us_mart_po/model_deployer_artifacts/pi-model`).
2. Open the app → **Deploy Model**; fill the form (walk through each field using
   `model_deployer_form_UI.txt` values).
3. **Deploy** → open `mlops_deploy_model_job` run → narrate wrapper/validator/deployer.
4. Endpoint reaches **Ready** → **Query**: `{"inputs":["Pregnancy Test","EKG"]}`
   → `{"predictions":["non-invasive","non-invasive"]}`. Compare to prod ground truth.
5. Show the UC model version, tags, and the inference table row.

## Hands-on exercises (Takeda drives)
1. Deploy a simple model (single `.pkl`) through the app.
2. Deploy a full MLflow folder (as-is path).
3. Break it on purpose: wrong artifact path → read the error → fix. (Teaches triage.)
4. Query the endpoint via REST with an SP token (see `ServicePrincipal_REST_Security.md`).
5. Find a failed run's logs: Jobs → run → task output; endpoint → Build logs.

## Triage drills (from Operations_Runbook §7)
- "No module named torch" → heavy-model validator behavior (now non-fatal).
- `scipy==1.10.1 not found` → old pins vs. private repo → use installable versions.
- "Failed to trigger deploy job: Bad Request" → transient, retry.
- Endpoint check failed → model missing preprocessing → deploy full MLflow folder.

## Exit criteria (handover is "done" when)
- [ ] A Takeda engineer deploys a model through the app unaided.
- [ ] They promote it to a higher env via bundle/CI.
- [ ] They locate deploy-job logs and endpoint build logs on their own.
- [ ] They can run the REST verification and read the inference table.
- [ ] Owners named for: app upgrades, UC grants, secret rotation, monitoring refresh.
- [ ] Open questions logged and assigned.

## Recording & artifacts
Record the session; store the recording + these docs in Takeda's team space and link from
the documentation package.
