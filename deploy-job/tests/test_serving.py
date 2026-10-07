"""Unit tests for model_deployer.serving helpers that don't call the workspace."""
from types import SimpleNamespace

from model_deployer import serving


def fake_ctx(form_tags, job_tags, uc_model="m"):
    return SimpleNamespace(
        spec={"tags": form_tags, "deployed_by": "me@x.com", "compute": {}},
        job_tags=lambda: job_tags, uc_model=uc_model)


def test_form_tags_win_and_come_first():
    tags = serving.build_tags(fake_ctx({"team": "form"}, {"team": "gov", "cost_center": "cc"}))
    assert [(t.key, t.value) for t in tags][:2] == [("team", "form"), ("cost_center", "cc")]


def test_tag_limit_drops_lowest_priority_first():
    form = {f"f{i}": i for i in range(15)}
    gov = {f"g{i}": i for i in range(5)}
    keys = [t.key for t in serving.build_tags(fake_ctx(form, gov))]
    assert len(keys) == serving.MAX_ENDPOINT_TAGS
    assert "deployed_by" not in keys and "f0" in keys and "g4" in keys


def test_serves_versions():
    ctx = fake_ctx({}, {})
    variants = [{"label": "A", "version": 4}, {"label": "B", "version": 7}]
    config = {"served_entities": [{"name": "m_B", "entity_version": "7"}, {"name": "m_A", "entity_version": "4"}]}
    assert serving.serves_versions(ctx, config, variants)
    assert not serving.serves_versions(ctx, config, variants[:1])
