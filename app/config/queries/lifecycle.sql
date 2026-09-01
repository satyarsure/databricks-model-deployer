-- @param lifecycle_table STRING = main.default.model_lifecycle_events
-- @param deployment_id STRING
-- Lifecycle event trail for one deployment (wrapper -> validator -> deployer).
-- The sample value lets type generation resolve columns from a generic empty table;
-- at runtime the app binds the real catalog.schema table via /api/config.
SELECT
  stage,
  status,
  message,
  CAST(event_time AS STRING) AS event_time
FROM IDENTIFIER(:lifecycle_table)
WHERE CAST(deployment_id AS STRING) = :deployment_id
ORDER BY event_time
