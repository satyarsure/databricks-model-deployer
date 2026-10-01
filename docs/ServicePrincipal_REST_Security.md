# Service-Principal REST Endpoint Security Specification + Verification

Covers Milestone 1.1 "Service-Principal REST Endpoint Security Specification" and
Milestone 1.2 "Service-Principal Auth & REST Access Verification" for the Model Serving
endpoints created by the Model Deployer.

## 1. Identities
| Identity | Purpose | Credential |
|---|---|---|
| **App service principal** (`app_sp`) | Runs the Databricks App server; reads/writes Lakebase; triggers the deploy job. | OAuth (M2M) managed by the app bundle |
| **Deploy-job run identity** | Does the real work: reads artifacts, registers UC models, creates/updates serving endpoints. Its grants are what matter. | Job run-as identity |
| **End user (OBO)** | Each app user acts with their own permissions via on-behalf-of auth. | SSO session |
| **Consumer SP** (callers of the REST endpoint) | External systems/apps that query the model. | OAuth token or PAT with endpoint `CAN_QUERY` |

## 2. REST endpoint auth model
- Model Serving endpoints are invoked at
  `POST https://<workspace-host>/serving-endpoints/<endpoint>/invocations`.
- **AuthN:** `Authorization: Bearer <token>` — either an **OAuth access token** for a
  service principal (preferred for machine-to-machine) or a PAT. OAuth tokens are
  short-lived; prefer them over long-lived PATs.
- **AuthZ:** the caller principal needs **`CAN_QUERY`** on the endpoint. Managing the
  endpoint (update config/traffic) needs **`CAN_MANAGE`**. Grant least privilege:
  consumers get `CAN_QUERY` only.
- **Transport:** TLS enforced by the platform. No endpoint is reachable without a valid
  bearer token.

## 3. Security requirements (the spec)
1. **No PATs for service-to-service.** Consumers authenticate as a **service principal via
   OAuth** (`client_credentials`); rotate secrets per Takeda policy.
2. **Least privilege:** consumer SP gets endpoint `CAN_QUERY` only; never `CAN_MANAGE`.
3. **Separate SP per consumer system** so access can be revoked independently and usage is
   attributable in logs.
4. **Audit:** enable AI Gateway **usage tracking** + **inference tables** so every request
   (principal, payload, response) is logged to a UC table.
5. **Secret handling:** store consumer SP secrets in the caller's secret manager; never in
   code or notebooks. Short TTL + rotation.
6. **Network:** rely on workspace IP access lists / PrivateLink if Takeda requires network
   restriction in addition to token auth.
7. **Data classification:** non-GxP only on these endpoints (matches the `nongxp` buckets).

## 4. Verification procedure (run after each env deploy)

### 4a. Get an OAuth token for the consumer SP
```bash
curl -s -X POST \
  https://<workspace-host>/oidc/v1/token \
  -u "<CLIENT_ID>:<CLIENT_SECRET>" \
  -d 'grant_type=client_credentials&scope=all-apis' | jq -r .access_token
```

### 4b. Positive test — authorized query returns correct prediction
```bash
TOKEN=<access_token_from_4a>
curl -s -X POST \
  https://<workspace-host>/serving-endpoints/protocol_intelligence_ui_endpoint/invocations \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"inputs": ["Pregnancy Test", "EKG"]}'
# EXPECT: {"predictions": ["non-invasive", "non-invasive"]}
```

### 4c. Negative test — no / wrong token is rejected
```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  https://<workspace-host>/serving-endpoints/protocol_intelligence_ui_endpoint/invocations \
  -H "Content-Type: application/json" \
  -d '{"inputs": ["Pregnancy Test"]}'
# EXPECT: 401/403 (unauthenticated/unauthorized)
```

### 4d. Negative test — token without CAN_QUERY is rejected
Use a token for an SP with no endpoint grant → EXPECT 403.

### 4e. Audit check
After 4b, confirm the request appears in the inference table:
```sql
SELECT * FROM <catalog>.<schema>.protocol_intelligence_ui_endpoint_payload
ORDER BY timestamp_ms DESC LIMIT 5;
```

## 5. Sign-off checklist (attach to the ticket)
- [ ] Consumer SP created, OAuth secret stored in secret manager
- [ ] SP granted `CAN_QUERY` only on the endpoint
- [ ] 4b positive test passes (correct predictions)
- [ ] 4c + 4d negative tests return 401/403
- [ ] 4e inference-table row present (audit works)
- [ ] Secret rotation schedule recorded
