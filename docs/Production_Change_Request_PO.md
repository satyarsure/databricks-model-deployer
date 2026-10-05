# Production Change Request — PO (Protocol-Intelligence) model on the Model Deployer

**Status:** DRAFT content for the Takeda change ticket. The change owner raises the ticket. Items
marked `<FILL>` are known only once test is live (catalog names, dates, run links). Raise the ticket
**in parallel with** the test promotion; attach the test evidence (section 6) before approval.

## 1. Summary
| Field | Value |
|---|---|
| Title | Deploy the Model Deployer platform to R&D Prod and promote the PO (Protocol-Intelligence) model |
| Change type | Normal, **non-GxP** (R&D non-GxP catalogs/buckets `…-nongxp-…`) |
| Change owner | `<FILL>` (engagement lead) |
| Implementer | `<FILL>` (Databricks engineer) with the deploy service principal via GitHub Actions |
| Requested window | `<FILL>` — no outage required (see section 4) |
| Related | Test promotion run `<FILL link>`; repo commit/tag `<FILL>` |

## 2. Description and justification
The PO model classifies a clinical procedure name as **invasive / non-invasive** (abbreviation
mapping → Bio_ClinicalBERT embeddings → linear SVM). Today it is served in prod by a manually created
endpoint (`DSI_AP_Automation_Protocol_Intelligence_AImodel`, from the legacy workspace model registry,
2023), with no repeatable deployment path, no Unity Catalog lineage, and no promotion controls.

This change moves it onto the standard **Model Deployer** framework:
- the model is registered in **Unity Catalog** (lineage, access control, audit), promoted **unchanged**
  from test (the same artifact that passed validation), and served on a Model Serving endpoint with
  usage tracking + inference tables, scale-to-zero, and governance tags;
- deployment is repeatable, through GitHub Actions with environment approvals, using Databricks
  Asset Bundles (no manual steps in prod).

It is the first model on the reusable path expected to onboard ~40–50 models over the next 12 months.

## 3. Scope (configuration items)
| Item | Change |
|---|---|
| R&D Prod workspace `<FILL host>` | New: Model Deployer deploy job + app (bundle target `prod`) |
| UC catalog/schema `<PROD_CATALOG>.<PROD_SCHEMA>` | New registered model `protocol_intelligence` (version copied from test) |
| Model Serving | New endpoint `protocol_intelligence_endpoint` (CPU Small, scale-to-zero) |
| Lakebase | New project for the app's status store (`<FILL>`) |
| GitHub | `prod` environment with required reviewers; secrets/variables per `docs/Test_Prod_Promotion_Setup.md` Step 5 |
| **Not changed** | The legacy endpoint `DSI_AP_Automation_Protocol_Intelligence_AImodel` and its consumers |

## 4. Impact and risk
- **No outage, no consumer impact:** the new endpoint runs **alongside** the legacy endpoint.
  Consumers keep calling the legacy endpoint until a separate, agreed cutover. Moving consumers
  and retiring the legacy endpoint are **out of scope** and need their own change.
- **Prediction parity** is proven before approval: the same artifact returned
  `{"predictions": ["non-invasive", "non-invasive"]}` for `{"inputs": ["Pregnancy Test", "EKG"]}` in
  dev and in test, matching the legacy prod endpoint.
- **Build risk (low):** the model needs torch/transformers. The serving image builds from the model's
  own requirements. This was proven on the platform's private Python repository in dev and is proven
  again in test before approval. First build takes ~30 min.
- **Access:** endpoint permissions are explicit (CAN_QUERY for the consuming application's service
  principal only — `docs/ServicePrincipal_REST_Security.md`); no data is moved; the model reads only
  the request payload.
- **Cost:** serverless, scale-to-zero, charged to the environment's usage policy and tags.

## 5. Implementation plan
| # | Step | Who | Evidence |
|---|---|---|---|
| 1 | Admin prerequisites done: catalog/schema/volume, grants (incl. read on the test model), Lakebase project, usage policy, deploy SP (`docs/Test_Prod_Promotion_Setup.md` Steps 1–4) | UC/workspace admin | Screenshot/SQL output |
| 2 | GitHub `prod` environment: secrets, variables, required reviewers (Step 5) | Repo admin | Settings screenshot |
| 3 | **Bundle Deploy** workflow, `environment = prod` (deploy job → app) | Implementer, approved by reviewer | Workflow run link |
| 4 | App status SUCCEEDED; open the prod app | Implementer | `databricks apps get` output |
| 5 | **Promote Model** workflow: `environment = prod`, `change_request = <this CR>`, source = test `protocol_intelligence@champion` | Implementer, approved by reviewer | Run summary (SUCCESS, endpoint reply) |
| 6 | Grant CAN_QUERY to the consuming application's SP; verify the positive/negative REST checks | Implementer | curl output (SP REST spec) |
| 7 | (Optional) enable Lakehouse Monitoring on the inference table | Implementer | Monitor dashboard link |

## 6. Pre-implementation evidence (from test)
- [ ] Bundle Deploy `qa` run: `<FILL link>` — SUCCESS
- [ ] Promote Model `qa` run: `<FILL link>` — SUCCESS; summary shows source `usdev_rnd_non_gxp.rnd_us_mart_po.protocol_intelligence_ui` v`<n>` → `<TEST_CATALOG>.<TEST_SCHEMA>.protocol_intelligence` v`<n>`, endpoint reply = expected
- [ ] Test cases passed in test: TC1 (platform smoke), TC14b (promotion), TC14c (gate rejects a wrong expected output)
- [ ] Parity with the legacy prod endpoint on the agreed validation set: `<FILL — list or attach>`
- [ ] Documentation package reviewed (`docs/Takeda_Documentation_Package.md`)

## 7. Post-implementation verification
1. The Promote Model run summary for `prod` shows **SUCCESS** and the re-queried reply
   `{"predictions": ["non-invasive", "non-invasive"]}`.
2. In Catalog Explorer, `<PROD_CATALOG>.<PROD_SCHEMA>.protocol_intelligence` v`<n>` has `@champion`
   and the tags `promoted_from_model` / `promoted_from_version` pointing at the test version.
3. The endpoint carries the tag `change_request = <this CR>`.
4. A query from the consuming application's SP succeeds; one from a non-granted identity is denied (403).
5. The prod app's Deployed Models row is **Complete** with a full lifecycle timeline.

## 8. Backout plan
No consumer uses the new endpoint during this change, so backout has no business impact:
1. Delete the new endpoint `protocol_intelligence_endpoint` (or leave it scaled to zero).
2. Optionally delete the model version / registered model in the prod catalog.
3. Optionally `databricks bundle destroy -t prod` for the app and job (INSTALL.md §9).
The legacy endpoint is untouched throughout. For a later bad **model** version (after cutover), roll
back by redeploying the previous version (app → New version → existing version, or the workflow with
`source_version` = the previous version).

## 9. Approvals
| Role | Name | Decision |
|---|---|---|
| Business owner (PO model) | `<FILL>` | |
| Model owner / developer | `<FILL>` | |
| Platform owner (R&D Databricks) | `<FILL>` | |
| Change advisory / release manager | `<FILL>` | |
