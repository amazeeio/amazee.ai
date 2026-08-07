from unittest.mock import patch

from app.db.models import (
    DBModel,
    DBModelAccessGroup,
    DBModelAccessGroupModel,
    DBModelAccessGroupRegion,
    DBModelAliasTarget,
    DBModelRegion,
)


def _payload(region_name: str) -> dict:
    return {
        "access_groups": [
            {
                "slug": "default-models",
                "label": "Default Models",
                "description": "Baseline group",
                "regions": [region_name],
            }
        ],
        "models": [
            {
                "model_id": "claude-sonnet",
                "display_name": "Claude Sonnet",
                "provider": "bedrock",
                "type": "chat",
                "description": "General chat",
                "litellm_params": {"model": "bedrock/anthropic.claude-sonnet"},
                "access_groups": ["default-models"],
                "deployments": [
                    {
                        "region": region_name,
                        "litellm_params_override": {"model": "bedrock/au.anthropic.claude-sonnet"},
                    }
                ],
            },
            {
                "model_id": "chat",
                "display_name": "Chat (alias)",
                "provider": "bedrock",
                "type": "chat",
                "is_alias": True,
                "access_groups": ["default-models"],
                "alias_targets": [{"region": region_name, "target": "claude-sonnet"}],
            },
        ],
    }


def _apply(client, admin_token, payload):
    return client.post(
        "/admin/models/apply",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=payload,
    )


@patch("app.services.model_sync.LiteLLMService")
def test_apply_creates_everything(mock_svc, client, admin_token, db, test_region):
    res = _apply(client, admin_token, _payload(test_region.name))
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["dry_run"] is False
    assert data["syncs_scheduled"] == 2  # model + alias, one region each
    actions = {(c["entity"], c["key"], c["action"]) for c in data["changes"]}
    assert ("access_group", "default-models", "create") in actions
    assert ("model", "claude-sonnet", "create") in actions
    assert ("model", "chat", "create") in actions
    assert ("deployment", f"claude-sonnet@{test_region.name}", "create") in actions
    assert ("deployment", f"chat@{test_region.name}", "create") in actions

    group = db.query(DBModelAccessGroup).filter_by(slug="default-models").one()
    assert db.query(DBModelAccessGroupRegion).filter_by(group_id=group.id).count() == 1
    model = db.query(DBModel).filter_by(model_id="claude-sonnet").one()
    alias = db.query(DBModel).filter_by(model_id="chat").one()
    assert alias.is_alias is True
    target = db.query(DBModelAliasTarget).filter_by(alias_model_id=alias.id).one()
    assert target.target_model_id == model.id
    assert db.query(DBModelAccessGroupModel).filter_by(group_id=group.id).count() == 2
    assoc = db.query(DBModelRegion).filter_by(model_id=model.id).one()
    assert assoc.is_active is True
    assert assoc.litellm_params_override == {"model": "bedrock/au.anthropic.claude-sonnet"}


@patch("app.services.model_sync.LiteLLMService")
def test_apply_is_idempotent(mock_svc, client, admin_token, db, test_region):
    payload = _payload(test_region.name)
    assert _apply(client, admin_token, payload).status_code == 200
    res = _apply(client, admin_token, payload)
    assert res.status_code == 200
    data = res.json()
    assert data["changes"] == []
    assert data["syncs_scheduled"] == 0


def test_apply_dry_run_writes_nothing(client, admin_token, db, test_region):
    payload = _payload(test_region.name)
    payload["dry_run"] = True
    res = _apply(client, admin_token, payload)
    assert res.status_code == 200
    data = res.json()
    assert data["dry_run"] is True
    assert data["syncs_scheduled"] == 2
    assert len(data["changes"]) > 0
    assert db.query(DBModel).filter_by(model_id="claude-sonnet").count() == 0
    assert db.query(DBModelAccessGroup).filter_by(slug="default-models").count() == 0


@patch("app.services.model_sync.LiteLLMService")
def test_apply_removes_deployment_declaratively(mock_svc, client, admin_token, db, test_region):
    payload = _payload(test_region.name)
    assert _apply(client, admin_token, payload).status_code == 200
    # Same model, no deployments -> region deactivated.
    payload["models"][0]["deployments"] = []
    res = _apply(client, admin_token, payload)
    assert res.status_code == 200
    actions = {(c["key"], c["action"]) for c in res.json()["changes"]}
    assert (f"claude-sonnet@{test_region.name}", "deactivate") in actions
    model = db.query(DBModel).filter_by(model_id="claude-sonnet").one()
    assoc = db.query(DBModelRegion).filter_by(model_id=model.id).one()
    db.refresh(assoc)
    assert assoc.is_active is False


@patch("app.services.model_sync.LiteLLMService")
def test_apply_prune_deactivates_absent_models(mock_svc, client, admin_token, db, test_region):
    payload = _payload(test_region.name)
    # Announce EOL (in the past) so the sunset guard allows the prune.
    payload["models"][1]["real_eol"] = "2020-01-01T00:00:00Z"
    assert _apply(client, admin_token, payload).status_code == 200

    pruned = _payload(test_region.name)
    pruned["models"] = [m for m in pruned["models"] if m["model_id"] == "claude-sonnet"]
    pruned["models"][0]["access_groups"] = ["default-models"]
    pruned["prune"] = True
    res = _apply(client, admin_token, pruned)
    assert res.status_code == 200
    actions = {(c["key"], c["action"]) for c in res.json()["changes"]}
    assert ("chat", "prune") in actions
    alias = db.query(DBModel).filter_by(model_id="chat").one()
    db.refresh(alias)
    assert alias.is_active_globally is False


@patch("app.services.model_sync.LiteLLMService")
def test_apply_prune_blocked_before_eol(mock_svc, client, admin_token, db, test_region):
    """Sunset protocol: prune must refuse a model with no announced EOL, or
    whose EOL has not passed yet — it stays active and is reported."""
    payload = _payload(test_region.name)
    payload["models"][0]["real_eol"] = "2099-01-01T00:00:00Z"  # future EOL
    assert _apply(client, admin_token, payload).status_code == 200

    pruned = {
        "prune": True,
        "access_groups": _payload(test_region.name)["access_groups"],
        "models": [
            {
                "model_id": "placeholder",
                "display_name": "Placeholder",
                "provider": "test",
                "type": "chat",
                "access_groups": ["default-models"],
            }
        ],
    }
    res = _apply(client, admin_token, pruned)
    assert res.status_code == 200
    blocked = {c["key"]: c["detail"] for c in res.json()["changes"] if c["action"] == "prune_blocked"}
    assert "no eol_date announced" in blocked["chat"]  # alias never announced
    assert "has not passed" in blocked["claude-sonnet"]  # future EOL
    sonnet = db.query(DBModel).filter_by(model_id="claude-sonnet").one()
    alias = db.query(DBModel).filter_by(model_id="chat").one()
    db.refresh(sonnet)
    db.refresh(alias)
    assert sonnet.is_active_globally is True
    assert alias.is_active_globally is True


@patch("app.services.model_sync.LiteLLMService")
def test_apply_without_prune_reports_unmanaged(mock_svc, client, admin_token, db, test_region):
    payload = _payload(test_region.name)
    assert _apply(client, admin_token, payload).status_code == 200

    partial = _payload(test_region.name)
    partial["models"] = [m for m in partial["models"] if m["model_id"] == "claude-sonnet"]
    res = _apply(client, admin_token, partial)
    assert res.status_code == 200
    data = res.json()
    assert data["unmanaged_models"] == ["chat"]
    alias = db.query(DBModel).filter_by(model_id="chat").one()
    assert alias.is_active_globally is True


def test_apply_unknown_region_rejected(client, admin_token, test_region):
    payload = _payload("no-such-region")
    res = _apply(client, admin_token, payload)
    assert res.status_code == 400
    assert "Unknown regions" in res.json()["detail"]


def test_apply_prune_with_empty_models_rejected(client, admin_token):
    res = _apply(client, admin_token, {"prune": True, "models": [], "access_groups": []})
    assert res.status_code == 400
    assert "Refusing to prune" in res.json()["detail"]


def test_apply_alias_chain_rejected(client, admin_token, test_region):
    payload = _payload(test_region.name)
    payload["models"].append(
        {
            "model_id": "chat2",
            "display_name": "Chat 2",
            "provider": "bedrock",
            "type": "chat",
            "is_alias": True,
            "alias_targets": [{"region": test_region.name, "target": "chat"}],
        }
    )
    res = _apply(client, admin_token, payload)
    assert res.status_code == 400
    assert "chains are not supported" in res.json()["detail"]


@patch("app.services.model_sync.LiteLLMService")
def test_apply_retarget_alias_triggers_resync(mock_svc, client, admin_token, db, test_region):
    payload = _payload(test_region.name)
    payload["models"].append(
        {
            "model_id": "claude-haiku",
            "display_name": "Claude Haiku",
            "provider": "bedrock",
            "type": "chat",
            "litellm_params": {"model": "bedrock/anthropic.claude-haiku"},
            "access_groups": ["default-models"],
            "deployments": [{"region": test_region.name}],
        }
    )
    assert _apply(client, admin_token, payload).status_code == 200

    # Point the alias at a different target: alias must be marked changed and resynced.
    payload["models"][1]["alias_targets"] = [{"region": test_region.name, "target": "claude-haiku"}]
    res = _apply(client, admin_token, payload)
    assert res.status_code == 200
    data = res.json()
    actions = {(c["entity"], c["key"], c["action"]) for c in data["changes"]}
    assert ("alias_target", "chat", "update") in actions
    assert data["syncs_scheduled"] == 1  # only the alias resyncs

    alias = db.query(DBModel).filter_by(model_id="chat").one()
    haiku = db.query(DBModel).filter_by(model_id="claude-haiku").one()
    target = db.query(DBModelAliasTarget).filter_by(alias_model_id=alias.id).one()
    assert target.target_model_id == haiku.id


@patch("app.services.model_sync.LiteLLMService")
def test_apply_override_change_resyncs_only_that_region(mock_svc, client, admin_token, db, test_region):
    from app.db.models import DBRegion

    region2 = DBRegion(
        name="second-region",
        litellm_api_url="https://second-litellm.com",
        litellm_api_key="key2",
        is_active=True,
    )
    db.add(region2)
    db.commit()

    payload = _payload(test_region.name)
    payload["models"][0]["deployments"].append({"region": "second-region"})
    payload["models"][1]["alias_targets"].append({"region": "second-region", "target": "claude-sonnet"})
    payload["access_groups"][0]["regions"].append("second-region")
    assert _apply(client, admin_token, payload).status_code == 200

    # Change only the first region's override — the second region must not resync.
    payload["models"][0]["deployments"][0]["litellm_params_override"] = {
        "model": "bedrock/au.anthropic.claude-sonnet-v2"
    }
    res = _apply(client, admin_token, payload)
    assert res.status_code == 200
    data = res.json()
    assert data["syncs_scheduled"] == 1
    keys = {c["key"] for c in data["changes"]}
    assert keys == {f"claude-sonnet@{test_region.name}"}


def test_apply_refuses_regions_the_catalog_does_not_manage(
    client, admin_token, db, test_region, monkeypatch
):
    """The prod gate: outside local, a region absent from CATALOG_MANAGED_REGIONS
    is rejected before anything is written — this is what keeps the catalog off
    private regions like ren2."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "ENV_SUFFIX", "production")
    monkeypatch.setattr(settings, "CATALOG_MANAGED_REGIONS", "")
    res = _apply(client, admin_token, _payload(test_region.name))
    assert res.status_code == 400
    assert "not managed by the model catalog" in res.json()["detail"]
    assert db.query(DBModel).count() == 0

    monkeypatch.setattr(settings, "CATALOG_MANAGED_REGIONS", f"other, {test_region.name}")
    with patch("app.services.model_sync.LiteLLMService"):
        assert _apply(client, admin_token, _payload(test_region.name)).status_code == 200


def test_apply_requires_admin(client, test_token, test_region):
    res = client.post(
        "/admin/models/apply",
        headers={"Authorization": f"Bearer {test_token}"},
        json=_payload(test_region.name),
    )
    assert res.status_code in (401, 403)
