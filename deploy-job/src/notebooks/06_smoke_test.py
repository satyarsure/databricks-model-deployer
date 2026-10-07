# Databricks notebook source
# MAGIC %md
# MAGIC # 5 · Smoke test (+ rollback)
# MAGIC Calls every served variant of the live endpoint. With a REAL payload (the form's sample input,
# MAGIC or the model's saved serving example) a failure **rolls the endpoint back** to its previous
# MAGIC config and fails the deployment. A synthetic schema example only produces a warning.

# COMMAND ----------
# Make the shared package (src/model_deployer) importable: it sits next to this notebooks folder.
import os, sys
_nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
_src = os.path.dirname(os.path.dirname(_nb if _nb.startswith("/Workspace") else "/Workspace" + _nb))
if _src not in sys.path:
    sys.path.insert(0, _src)
from model_deployer.context import Ctx
ctx = Ctx(dbutils, spark)

# COMMAND ----------
import json
import mlflow
from mlflow.models import convert_input_example_to_serving_input
from model_deployer import serving
from model_deployer.contract import Contract, build_input_example

mlflow.set_registry_uri("databricks-uc")
vv = ctx.get_value("register", "variant_versions", [])
ctx.version_str = ",".join(f'{v["label"]}:{v["version"]}' for v in vv)
ctx.merge_status(stage="smoke_test")
contract = Contract(ctx.spec)

# Guard: the endpoint must still serve exactly the versions this run deployed. If it doesn't (e.g. a
# previous attempt of this task already rolled it back), fail rather than test — and pass — the
# restored version, which would let finalize mark this deployment COMPLETE.
_current = serving.capture_config(ctx.w, ctx.endpoint_name)
if not (_current and serving.serves_versions(ctx, _current, vv)):
    raise RuntimeError("endpoint no longer serves this deployment's versions — likely already rolled "
                       "back by an earlier attempt of this task")


def payload_for(v):
    """(payload, real?) — the form's sample, else the model's saved serving example, else synthetic."""
    if contract.sample_payload is not None:
        return contract.sample_payload, True
    try:
        p = mlflow.artifacts.download_artifacts(f"models:/{ctx.uc_full}/{v['version']}/serving_input_example.json")
        return json.load(open(p)), True
    except Exception:
        pass
    ex = build_input_example(contract.input_schema)
    if ex is None:
        return None, False
    return json.loads(convert_input_example_to_serving_input(ex)), False


failures, warnings, oks = [], [], []
for v in vv:
    name = serving.entity_name(ctx, v["label"])
    payload, real = payload_for(v)
    if payload is None:
        warnings.append(f"{v['label']}: no payload to test with")
        continue
    try:
        resp = serving.query_served_model(ctx, name, payload)
        oks.append(f"{v['label']}: {str(resp)[:120]}")
    except Exception as e:
        (failures if real else warnings).append(f"{v['label']}: {str(e)[:300]}")

for wmsg in warnings:
    ctx.log_event("smoke_test", "IN_PROGRESS", f"warning (synthetic example, not rolled back): {wmsg}")
if failures:
    previous = ctx.get_value("deploy_endpoint", "previous_config")
    if previous:
        serving.rollback(ctx, previous)
        ctx.log_event("rollback", "IN_PROGRESS", "smoke test failed: restored the previous endpoint config")
    else:
        ctx.log_event("rollback", "IN_PROGRESS", "smoke test failed on a NEW endpoint: no previous config to restore")
    raise RuntimeError("smoke test failed: " + "; ".join(failures))
ctx.log_event("smoke_test", "IN_PROGRESS", "endpoint answered: " + ("; ".join(oks) or "nothing to test"))
