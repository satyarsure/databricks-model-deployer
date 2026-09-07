-- @param lifecycle_table STRING = main.default.model_lifecycle_events
-- @param deployment_id STRING
-- @param refresh_nonce STRING = 0
-- Lifecycle event trail for one deployment (wrapper -> validator -> deployer).
-- The sample value lets type generation resolve columns from a generic empty table;
-- at runtime the app binds the real catalog.schema table via /api/config.
-- refresh_nonce is a cache-busting/re-execution nonce (changed by the client on each
-- poll); the no-op predicate below binds it without filtering events.
SELECT
  stage,
  status,
  message,
  CAST(event_time AS STRING) AS event_time
FROM IDENTIFIER(:lifecycle_table)
WHERE CAST(deployment_id AS STRING) = :deployment_id
  AND (:refresh_nonce IS NULL OR :refresh_nonce IS NOT NULL)
ORDER BY event_time
