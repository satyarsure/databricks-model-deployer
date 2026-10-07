"""Serving endpoint: create/update (A/B traffic, compute, scale-to-zero, tags, budget policy),
AI Gateway, access permissions, and the post-deploy smoke test with rollback."""
import inspect
import re
import time
from datetime import timedelta

from databricks.sdk import errors
from databricks.sdk.service.serving import (
    AiGatewayInferenceTableConfig, AiGatewayUsageTrackingConfig, EndpointCoreConfigInput,
    EndpointTag, Route, ServedEntityInput, TrafficConfig,
)

# The workload-type enum was renamed across SDK versions; import defensively.
try:
    from databricks.sdk.service.serving import ServingModelWorkloadType
except ImportError:
    from databricks.sdk.service.serving import ServedModelInputWorkloadType as ServingModelWorkloadType

MAX_ENDPOINT_TAGS = 20
_PERM_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def entity_name(ctx, label):
    return re.sub(r"[^A-Za-z0-9_-]", "_", f"{ctx.uc_model}_{label}")[:60]


def build_served_entities(ctx, variant_versions):
    compute = ctx.spec.get("compute", {}) or {}
    size = str(compute.get("size", "SMALL")).upper()
    if str(compute.get("compute_type", "cpu")).lower() == "gpu":
        workload_type = {"SMALL": "GPU_SMALL", "MEDIUM": "GPU_MEDIUM", "LARGE": "GPU_LARGE"}.get(size, "GPU_SMALL")
        workload_size = "Small"
    else:
        workload_type = "CPU"
        workload_size = {"SMALL": "Small", "MEDIUM": "Medium", "LARGE": "Large"}.get(size, "Small")
    scale_to_zero = bool(compute.get("scale_to_zero", True))
    entities, routes = [], []
    for v in variant_versions:
        name = entity_name(ctx, v["label"])
        entities.append(ServedEntityInput(
            entity_name=ctx.uc_full, entity_version=str(v["version"]), name=name,
            workload_type=ServingModelWorkloadType(workload_type), workload_size=workload_size,
            scale_to_zero_enabled=scale_to_zero))
        routes.append(Route(served_model_name=name, traffic_percentage=int(v.get("traffic_percent", 0))))
    total = sum(r.traffic_percentage for r in routes)
    if total != 100 and routes:
        routes[0].traffic_percentage += (100 - total)
    return entities, routes


def build_tags(ctx):
    """Endpoint tags in PRIORITY order for the 20-tag limit (over 20 the tags API rejects the whole
    request, so keep the first 20): (1) the UI/form tags, (2) the governance/job tags, (3) the
    auto-added gpu_type / application / deployed_by. First writer of a key wins."""
    values, order = {}, []

    def _tag(k, v):
        k = str(k)
        if k not in values:
            values[k] = str(v)
            order.append(k)

    for k, v in (ctx.spec.get("tags", {}) or {}).items():
        _tag(k, v)
    for k, v in ctx.job_tags().items():
        _tag(k, v)
    compute = ctx.spec.get("compute", {}) or {}
    if compute.get("gpu_type"):
        _tag("gpu_type", compute.get("gpu_type"))
    _tag("application", "mlops_model_deployer")
    _tag("deployed_by", ctx.spec.get("deployed_by", ""))
    if len(order) > MAX_ENDPOINT_TAGS:
        print(f"[deployer] endpoint tag limit is {MAX_ENDPOINT_TAGS}; have {len(values)} — "
              f"UI/form tags prioritized; dropping lowest-priority: {order[MAX_ENDPOINT_TAGS:]}")
        order = order[:MAX_ENDPOINT_TAGS]
    return [EndpointTag(key=k, value=values[k]) for k in order]


def capture_config(w, endpoint_name):
    """The endpoint's current served entities + traffic (for rollback), or None if it doesn't exist."""
    try:
        ep = w.serving_endpoints.get(endpoint_name)
    except errors.platform.ResourceDoesNotExist:
        return None
    cfg = ep.config
    if not cfg or not cfg.served_entities:
        return None
    return {
        "served_entities": [e.as_dict() for e in cfg.served_entities],
        "traffic_config": cfg.traffic_config.as_dict() if cfg.traffic_config else None,
    }


def serves_versions(ctx, config, variant_versions):
    """Does a captured config serve exactly these variants (served-entity name + version)?"""
    have = sorted((e.get("name"), str(e.get("entity_version"))) for e in config.get("served_entities", []))
    want = sorted((entity_name(ctx, v["label"]), str(v["version"])) for v in variant_versions)
    return have == want


def deploy(ctx, variant_versions):
    """Create or update the endpoint. Returns True if it was newly created."""
    w = ctx.w
    entities, routes = build_served_entities(ctx, variant_versions)
    tags = build_tags(ctx)
    # Tags ARE synced on update (tags API). budget_policy_id + description are create-time only.
    budget_policy_id = ctx.policy or None
    description = ctx.spec.get("description") or None
    try:
        w.serving_endpoints.get(ctx.endpoint_name)
        print(f"[deployer] updating {ctx.endpoint_name} (budget policy/description stay as first created)")
        w.serving_endpoints.update_config_and_wait(
            name=ctx.endpoint_name, served_entities=entities,
            traffic_config=TrafficConfig(routes=routes), timeout=timedelta(minutes=60))
        try:
            existing = w.serving_endpoints.get(ctx.endpoint_name)
            desired = {t.key for t in tags}
            delete_keys = [t.key for t in (existing.tags or []) if t.key not in desired]
            kw = {"name": ctx.endpoint_name, "add_tags": tags}
            if delete_keys:
                kw["delete_tags"] = delete_keys
            w.serving_endpoints.patch(**kw)
            print(f"[deployer] synced endpoint tags ({len(tags)} set, {len(delete_keys)} removed)")
        except Exception as te:
            print(f"[deployer] tag sync warning (non-fatal): {te}")
        created = False
    except errors.platform.ResourceDoesNotExist:
        print(f"[deployer] creating {ctx.endpoint_name} (budget_policy_id={budget_policy_id})")
        config = EndpointCoreConfigInput(name=ctx.endpoint_name, served_entities=entities,
                                         traffic_config=TrafficConfig(routes=routes))
        kw = dict(name=ctx.endpoint_name, config=config, timeout=timedelta(minutes=60))
        if budget_policy_id:
            kw["budget_policy_id"] = budget_policy_id
        if description:
            kw["description"] = description
        # Drop kwargs the installed SDK doesn't accept, so an SDK change degrades gracefully.
        allowed = set(inspect.signature(w.serving_endpoints.create_and_wait).parameters)
        dropped = [k for k in kw if k not in allowed]
        if dropped:
            print(f"[deployer] WARNING: databricks-sdk create_and_wait does not accept {dropped}; skipping.")
            kw = {k: v for k, v in kw.items() if k in allowed}
        w.serving_endpoints.create_and_wait(**kw)
        try:
            w.serving_endpoints.patch(name=ctx.endpoint_name, add_tags=tags)
            print(f"[deployer] applied {len(tags)} endpoint tags")
        except Exception as te:
            print(f"[deployer] endpoint tag apply warning (non-fatal): {te}")
        created = True
    enable_ai_gateway(ctx)
    apply_permissions(ctx)
    return created


def rollback(ctx, previous):
    """Restore the endpoint's previous served entities + traffic."""
    entities = [ServedEntityInput.from_dict(d) for d in previous["served_entities"]]
    traffic = TrafficConfig.from_dict(previous["traffic_config"]) if previous.get("traffic_config") else None
    ctx.w.serving_endpoints.update_config_and_wait(
        name=ctx.endpoint_name, served_entities=entities, traffic_config=traffic,
        timeout=timedelta(minutes=60))


def enable_ai_gateway(ctx):
    """Usage tracking + payload inference tables. Idempotent and self-healing (a table left behind
    by a prior deployment of the same endpoint name -> retry with a per-deployment prefix, then
    usage-tracking only). Non-fatal."""
    w, name = ctx.w, ctx.endpoint_name
    try:
        cur = w.serving_endpoints.get(name).ai_gateway
        if (cur and cur.usage_tracking_config and cur.usage_tracking_config.enabled
                and cur.inference_table_config and cur.inference_table_config.enabled):
            print("[deployer] AI Gateway already enabled; leaving as-is")
            return
    except Exception:
        pass

    def _put(prefix):
        kw = {"name": name, "usage_tracking_config": AiGatewayUsageTrackingConfig(enabled=True)}
        if prefix is not None:
            kw["inference_table_config"] = AiGatewayInferenceTableConfig(
                catalog_name=ctx.catalog, schema_name=ctx.schema, table_name_prefix=prefix, enabled=True)
        w.serving_endpoints.put_ai_gateway(**kw)

    try:
        _put(f"{name}_payload")
        print("[deployer] AI Gateway: usage tracking + inference tables enabled")
    except Exception as ge:
        if "already exists" in str(ge).lower():
            alt = f"{name}_{ctx.deployment_id}"
            try:
                _put(alt)
                print(f"[deployer] AI Gateway enabled with fresh inference-table prefix '{alt}'")
            except Exception as ge2:
                try:
                    _put(None)
                    print(f"[deployer] AI Gateway: usage tracking enabled; inference table skipped ({ge2})")
                except Exception as ge3:
                    print(f"[deployer] AI Gateway warning (non-fatal): {ge3}")
        else:
            print(f"[deployer] AI Gateway warning (non-fatal): {ge}")


def apply_permissions(ctx):
    """spec.permissions {"can_manage"|"can_query"|"can_view": [principals]} honored as given; the UI
    submitter gets CAN_MANAGE by default unless listed. Email -> user, UUID -> SP, else group.
    PATCH (merge) so the owner and existing ACLs are preserved. Non-fatal."""
    try:
        from databricks.sdk.service.serving import (
            ServingEndpointAccessControlRequest as _ACR,
            ServingEndpointPermissionLevel as _PL,
        )
    except Exception as ie:
        print(f"[deployer] permissions skipped (SDK lacks serving permission types): {ie}")
        return
    perms = ctx.spec.get("permissions") or {}
    level_by_key = {"can_manage": _PL.CAN_MANAGE, "can_query": _PL.CAN_QUERY, "can_view": _PL.CAN_VIEW}
    rank = {_PL.CAN_VIEW: 1, _PL.CAN_QUERY: 2, _PL.CAN_MANAGE: 3}
    wanted = {}
    for key, lvl in level_by_key.items():
        for p in (perms.get(key) or []):
            p = str(p).strip()
            if p and (p not in wanted or rank[lvl] > rank[wanted[p]]):
                wanted[p] = lvl
    deployer = (ctx.spec.get("deployed_by") or "").strip()
    if deployer and deployer not in wanted:
        wanted[deployer] = _PL.CAN_MANAGE
    if not wanted:
        return
    acl = []
    for p, lvl in wanted.items():
        if "@" in p:
            acl.append(_ACR(user_name=p, permission_level=lvl))
        elif _PERM_UUID.match(p):
            acl.append(_ACR(service_principal_name=p, permission_level=lvl))
        else:
            acl.append(_ACR(group_name=p, permission_level=lvl))
    try:
        ep = ctx.w.serving_endpoints.get(ctx.endpoint_name)
        ctx.w.serving_endpoints.update_permissions(serving_endpoint_id=ep.id, access_control_list=acl)
        print("[deployer] applied endpoint permission(s): " + ", ".join(f"{p}={l.value}" for p, l in wanted.items()))
        ctx.log_event("deployer", "IN_PROGRESS", f"applied {len(acl)} endpoint permission(s)")
    except Exception as pe:
        print(f"[deployer] permissions warning (non-fatal): {pe}")
        ctx.log_event("deployer", "IN_PROGRESS", f"permission apply warning: {str(pe)[:200]}")


# Errors worth waiting out: a scale-to-zero endpoint waking up, or a container still loading.
_TRANSIENT = re.compile(r"\b(429|502|503|504)\b|scal|not ready|temporarily|timed? ?out|unavailable", re.I)


def query_served_model(ctx, served_model, payload, wait_minutes=15):
    """POST the payload to one served model of the endpoint (so every A/B variant is tested),
    retrying transient errors while a scaled-to-zero endpoint wakes up."""
    path = f"/serving-endpoints/{ctx.endpoint_name}/served-models/{served_model}/invocations"
    deadline = time.time() + wait_minutes * 60
    while True:
        try:
            return ctx.w.api_client.do("POST", path, body=payload)
        except Exception as e:
            if not _TRANSIENT.search(str(e)) or time.time() > deadline:
                raise
            print(f"[smoke_test] {served_model}: transient error, retrying in 30s: {str(e)[:160]}")
            time.sleep(30)
