# Databricks notebook source
# MAGIC %md
# MAGIC # 4 · Deploy
# MAGIC Puts the registered variants live, checks they answer, and only then promotes them:
# MAGIC 1. **Remember** what the endpoint serves now (to roll back to).
# MAGIC 2. **Create / update** the endpoint — A/B traffic, compute, scale-to-zero, tags, budget
# MAGIC    policy, AI Gateway inference tables, access permissions.
# MAGIC 3. **Test** every served variant on the live endpoint. A real request (the form's sample, or
# MAGIC    an MLflow model folder's own example) that fails → **roll back** to step 1's config and fail.
# MAGIC    A synthetic schema request that fails → warning only.
# MAGIC 4. **Promote**: set `@champion` and mark the deployment COMPLETE.
# MAGIC
# MAGIC Timeline stage: **deployer**.

# COMMAND ----------
# MAGIC %run ./_setup

# COMMAND ----------
# MAGIC %md ## 1. Remember the current endpoint config

# COMMAND ----------
from model_deployer import serving

variant_versions = ctx.get_value("register", "variant_versions", [])
ctx.version_str = ",".join(f'{v["label"]}:{v["version"]}' for v in variant_versions)
ctx.merge_status(status="DEPLOYING", stage="deployer")

previous = serving.capture_config(ctx.w, ctx.endpoint_name)
if previous and serving.serves_versions(ctx, previous, variant_versions):
    # Serverless re-ran this task after an earlier attempt already updated the endpoint: what it
    # serves now is THIS deployment, so there is nothing safe to roll back to.
    ctx.log_event("deployer", "DEPLOYING", "task retry: endpoint already updated; no rollback target")
    previous = None

# COMMAND ----------
# MAGIC %md ## 2. Create / update the endpoint

# COMMAND ----------
ctx.log_event("deployer", "DEPLOYING", "creating/updating serving endpoint")
created = serving.deploy(ctx, variant_versions)
host = spark.conf.get("spark.databricks.workspaceUrl")
invoke_url = f"https://{host}/serving-endpoints/{ctx.endpoint_name}/invocations"
ctx.merge_status(invoke_url=invoke_url)
ctx.log_event("deployer", "DEPLOYING", f"endpoint {'created' if created else 'updated'}; testing it")

# COMMAND ----------
# MAGIC %md ## 3. Test every served variant (roll back on a real failure)

# COMMAND ----------
failures = []
for v in variant_versions:
    request, real = serving.test_payload(ctx, v)
    if request is None:
        ctx.log_event("deployer", "DEPLOYING", f"endpoint test: variant {v['label']}: no request to test with")
        continue
    try:
        answer = serving.query_served_model(ctx, serving.entity_name(ctx, v["label"]), request)
        ctx.log_event("deployer", "DEPLOYING", f"endpoint test: variant {v['label']} answered: {str(answer)[:150]}")
    except Exception as e:
        if real:
            failures.append(f"variant {v['label']}: {str(e)[:300]}")
        else:
            ctx.log_event("deployer", "DEPLOYING", f"endpoint test warning (synthetic schema request, "
                                                   f"not rolled back): variant {v['label']}: {str(e)[:260]}")

if failures:
    if previous:
        serving.rollback(ctx, previous)
        ctx.log_event("deployer", "DEPLOYING", "rollback: endpoint test failed — restored the previous endpoint config")
    else:
        ctx.log_event("deployer", "DEPLOYING", "rollback: endpoint test failed — nothing to restore (new endpoint)")
    raise RuntimeError("endpoint test failed: " + "; ".join(failures))

# COMMAND ----------
# MAGIC %md ## 4. Promote: @champion + COMPLETE

# COMMAND ----------
champion = serving.promote(ctx, variant_versions)
ctx.merge_status(status="COMPLETE", stage="deployed", error_message=None)
ctx.log_event("deployer", "COMPLETE", f"@champion -> v{champion}; {invoke_url}")
