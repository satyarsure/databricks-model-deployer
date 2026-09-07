-- @param deployments_table STRING = main.default.model_deployments
-- @param uc_full STRING = main.default.model
-- @param refresh_nonce STRING = 0
-- Distinct registered version numbers this app has deployed for a given UC model,
-- newest first. Populates the "variant A" version picker when starting an A/B test
-- (champion vs. challenger) against an already-deployed model. model_version is stored
-- as a comma-joined "label:version" string (e.g. "A:7" or "A:1,B:2"), so we explode it
-- and pull the trailing version number from each entry. refresh_nonce is a no-op
-- cache-buster (see deployments.sql).
SELECT DISTINCT
  CAST(regexp_extract(trim(variant), '([0-9]+)$', 1) AS INT) AS version
FROM (
  SELECT explode(split(model_version, ',')) AS variant
  FROM IDENTIFIER(:deployments_table)
  WHERE uc_full_name = :uc_full
    AND status = 'COMPLETE'
    AND model_version IS NOT NULL
    AND (:refresh_nonce IS NULL OR :refresh_nonce IS NOT NULL)
)
WHERE regexp_extract(trim(variant), '([0-9]+)$', 1) <> ''
ORDER BY version DESC
