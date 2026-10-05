# Model promotions (dev → qa → prod)

One JSON file per model. The **Promote Model** workflow (`.github/workflows/promote-model.yml`)
reads it, runs the target environment's Model Deployer deploy job, and fails unless the new
endpoint returns `sample_output` for `sample_input`. The promoted version is **copied unchanged**
from the lower environment's catalog (`MlflowClient.copy_model_version`), so the artifact served
in qa/prod is the one already validated below it. Nothing is rebuilt.

Catalog and schema names are **not** in these files. They come from each GitHub environment's
variables (`UC_CATALOG`/`UC_SCHEMA` = where to promote to, `SOURCE_UC_CATALOG`/`SOURCE_UC_SCHEMA`
= where to promote from), so the same file serves every hop.

| Field | Meaning |
|---|---|
| `name`, `description` | Shown in the Model Deployer app (Deployed Models) |
| `uc_model` | Model name in the target catalog/schema |
| `endpoint_name` | Serving endpoint to create/update in the target workspace |
| `source.<env>.model` | Model name in the **source** catalog/schema for that hop |
| `source.<env>.version` | Version number, alias (e.g. `champion`), or `latest` — can be overridden per run |
| `sample_input` / `sample_output` | The serving request and the exact expected reply (the promotion gate) |
| `compute` | `compute_type` `cpu`/`gpu`, `size` `SMALL`/`MEDIUM`/`LARGE`, `scale_to_zero` |
| `tags` | Extra endpoint tags (governance tags come from the deploy job automatically) |
| `permissions` | Optional endpoint ACL: `{"can_query": ["group-or-user"], "can_manage": [...]}` |

To add a model: copy `protocol_intelligence.json`, change the fields, open a PR. After the merge,
run **Actions → Promote Model**. See `docs/Test_Prod_Promotion_Setup.md` for the one-time setup.
