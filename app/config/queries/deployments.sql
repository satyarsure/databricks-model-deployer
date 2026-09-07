-- @param deployments_table STRING = main.default.model_deployments
-- @param refresh_nonce STRING = 0
-- The sample value above lets type generation resolve columns at build time from a
-- generic empty table; at runtime the app binds the real catalog.schema table via
-- /api/config (derived from the bound deploy job's parameters).
-- refresh_nonce is a cache-busting/re-execution nonce: the client changes it on each
-- poll (and per page load) so the analytics query returns fresh rows instead of a
-- cached result while a deployment is in progress. It does not affect the result set.
SELECT
  CAST(deployment_id AS STRING)      AS deployment_id,
  model_name,
  description,
  uc_full_name,
  uc_catalog,
  uc_schema,
  uc_model,
  model_version,
  status,
  stage,
  error_message,
  endpoint_name,
  invoke_url,
  experiment_name,
  eval_dataset,
  serverless_usage_policy,
  tags,
  artifacts_json,
  input_schema_json,
  output_schema_json,
  compute_type,
  gpu_type,
  compute_size,
  CAST(scale_to_zero AS STRING)      AS scale_to_zero,
  deployed_by,
  CAST(deployed_date AS STRING)      AS deployed_date,
  CAST(updated_at AS STRING)         AS updated_at
FROM IDENTIFIER(:deployments_table)
-- No-op predicate that binds refresh_nonce (always TRUE) so a changing nonce forces
-- a cache miss / re-execution without filtering any rows.
WHERE (:refresh_nonce IS NULL OR :refresh_nonce IS NOT NULL)
ORDER BY deployed_date DESC NULLS LAST, updated_at DESC NULLS LAST
LIMIT 200
