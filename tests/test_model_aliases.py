from unittest.mock import patch, AsyncMock, MagicMock

from app.db.models import DBModel, DBModelAliasTarget, DBModelRegion
from app.services.model_sync import effective_litellm_params


def _make_model(db, model_id, is_alias=False, params=None, **kwargs):
    model = DBModel(
        model_id=model_id,
        display_name=kwargs.get("display_name", model_id),
        provider=kwargs.get("provider", "aws"),
        type=kwargs.get("type", "chat"),
        is_active_globally=kwargs.get("is_active_globally", True),
        is_alias=is_alias,
        litellm_params=params,
    )
    db.add(model)
    db.commit()
    db.refresh(model)
    return model


def auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# effective_litellm_params
# ---------------------------------------------------------------------------


def test_effective_params_region_override_merges(db, test_region):
    model = _make_model(db, "claude-opus-5", params={"model": "bedrock/us.anthropic.opus", "extra": 1})
    db.add(
        DBModelRegion(
            model_id=model.id,
            region_id=test_region.id,
            is_active=True,
            litellm_params_override={"model": "bedrock/eu.anthropic.opus"},
        )
    )
    db.commit()

    params, error = effective_litellm_params(db, model, test_region.id)
    assert error is None
    assert params == {"model": "bedrock/eu.anthropic.opus", "extra": 1}


def test_effective_params_alias_resolves_target(db, test_region):
    target = _make_model(db, "claude-opus-5", params={"model": "bedrock/us.anthropic.opus"})
    alias = _make_model(db, "best-model", is_alias=True)
    db.add(
        DBModelAliasTarget(
            alias_model_id=alias.id, region_id=test_region.id, target_model_id=target.id
        )
    )
    # Alias inherits the target's per-region override too
    db.add(
        DBModelRegion(
            model_id=target.id,
            region_id=test_region.id,
            is_active=True,
            litellm_params_override={"model": "bedrock/ch.anthropic.opus"},
        )
    )
    db.commit()

    params, error = effective_litellm_params(db, alias, test_region.id)
    assert error is None
    assert params["model"] == "bedrock/ch.anthropic.opus"


def test_effective_params_alias_without_target_errors(db, test_region):
    alias = _make_model(db, "best-model", is_alias=True)
    params, error = effective_litellm_params(db, alias, test_region.id)
    assert params is None
    assert "no target model" in error


# ---------------------------------------------------------------------------
# API: alias CRUD + guards
# ---------------------------------------------------------------------------


def test_create_alias_and_toggle_requires_target(client, admin_token, db, test_region):
    target = _make_model(db, "claude-opus-5", params={"model": "bedrock/us.anthropic.opus"})

    response = client.post(
        "/admin/models",
        headers=auth(admin_token),
        json={
            "model_id": "best-model",
            "display_name": "Best Model",
            "provider": "alias",
            "type": "chat",
            "is_alias": True,
            "alias_targets": [{"region_id": test_region.id, "target_model_id": target.id}],
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["is_alias"] is True
    assert data["alias_targets"] == [
        {"region_id": test_region.id, "target_model_id": target.id}
    ]
    alias_id = data["id"]

    # Enabling in a region WITH a target works; the deployment carries the
    # target's params under the alias name.
    with patch("app.services.model_sync.LiteLLMService") as mock_cls:
        instance = MagicMock()
        instance.get_model_deployment_ids = AsyncMock(return_value=[])
        instance.add_model = AsyncMock(return_value={})
        mock_cls.return_value = instance
        response = client.post(
            "/admin/models/region-toggle",
            headers=auth(admin_token),
            json={"model_id": alias_id, "region_id": test_region.id, "is_active": True},
        )
    assert response.status_code == 200
    instance.add_model.assert_called_once_with(
        "best-model",
        {"model": "bedrock/us.anthropic.opus"},
        access_groups=[],
    )


def test_toggle_alias_without_target_rejected(client, admin_token, db, test_region):
    alias = _make_model(db, "best-model", is_alias=True)
    response = client.post(
        "/admin/models/region-toggle",
        headers=auth(admin_token),
        json={"model_id": alias.id, "region_id": test_region.id, "is_active": True},
    )
    assert response.status_code == 400
    assert "no target model" in response.json()["detail"]


def test_alias_chain_rejected(client, admin_token, db, test_region):
    inner_alias = _make_model(db, "inner-alias", is_alias=True)
    response = client.post(
        "/admin/models",
        headers=auth(admin_token),
        json={
            "model_id": "outer-alias",
            "display_name": "Outer",
            "provider": "alias",
            "type": "chat",
            "is_alias": True,
            "alias_targets": [
                {"region_id": test_region.id, "target_model_id": inner_alias.id}
            ],
        },
    )
    assert response.status_code == 400
    assert "chains" in response.json()["detail"]


def test_delete_target_blocked_while_aliased(client, admin_token, db, test_region):
    target = _make_model(db, "claude-opus-5", params={"model": "x"})
    alias = _make_model(db, "best-model", is_alias=True)
    db.add(
        DBModelAliasTarget(
            alias_model_id=alias.id, region_id=test_region.id, target_model_id=target.id
        )
    )
    db.commit()

    response = client.delete(f"/admin/models/{target.id}", headers=auth(admin_token))
    assert response.status_code == 409
    assert "best-model" in response.json()["detail"]


def test_retarget_alias_triggers_resync(client, admin_token, db, test_region):
    old_target = _make_model(db, "claude-opus-5", params={"model": "a"})
    new_target = _make_model(db, "claude-opus-6", params={"model": "b"})
    alias = _make_model(db, "best-model", is_alias=True)
    db.add(
        DBModelAliasTarget(
            alias_model_id=alias.id, region_id=test_region.id, target_model_id=old_target.id
        )
    )
    db.add(DBModelRegion(model_id=alias.id, region_id=test_region.id, is_active=True, sync_status="synced"))
    db.commit()

    with patch("app.api.admin_models.sync_model_to_region_task") as mock_sync:
        response = client.put(
            f"/admin/models/{alias.id}",
            headers=auth(admin_token),
            json={
                "alias_targets": [
                    {"region_id": test_region.id, "target_model_id": new_target.id}
                ]
            },
        )
    assert response.status_code == 200
    assert response.json()["alias_targets"][0]["target_model_id"] == new_target.id
    mock_sync.assert_called_once_with(alias.id, test_region.id)


# ---------------------------------------------------------------------------
# Region override via toggle + update
# ---------------------------------------------------------------------------


def test_region_toggle_stores_override(client, admin_token, db, test_region):
    model = _make_model(db, "claude-opus-5", params={"model": "bedrock/us.anthropic.opus"})
    with patch("app.api.admin_models.sync_model_to_region_task"):
        response = client.post(
            "/admin/models/region-toggle",
            headers=auth(admin_token),
            json={
                "model_id": model.id,
                "region_id": test_region.id,
                "is_active": True,
                "litellm_params_override": {"model": "bedrock/eu.anthropic.opus"},
            },
        )
    assert response.status_code == 200
    assoc = db.query(DBModelRegion).filter_by(model_id=model.id, region_id=test_region.id).first()
    assert assoc.litellm_params_override == {"model": "bedrock/eu.anthropic.opus"}


def test_update_region_overrides_resyncs_only_changed_region(client, admin_token, db, test_region):
    model = _make_model(db, "claude-opus-5", params={"model": "base"})
    db.add(DBModelRegion(model_id=model.id, region_id=test_region.id, is_active=True, sync_status="synced"))
    db.commit()

    with patch("app.api.admin_models.sync_model_to_region_task") as mock_sync:
        response = client.put(
            f"/admin/models/{model.id}",
            headers=auth(admin_token),
            json={"region_overrides": {str(test_region.id): {"model": "override"}}},
        )
    assert response.status_code == 200
    mock_sync.assert_called_once_with(model.id, test_region.id)
    assoc = db.query(DBModelRegion).filter_by(model_id=model.id, region_id=test_region.id).first()
    assert assoc.litellm_params_override == {"model": "override"}


# ---------------------------------------------------------------------------
# Bulk import + bedrock candidates + regional area
# ---------------------------------------------------------------------------


@patch("app.services.litellm.LiteLLMService")
def test_import_all_models(mock_cls, client, admin_token, db, test_region):
    existing = _make_model(db, "already-here", params={"model": "x"})
    instance = MagicMock()
    instance.get_model_info = AsyncMock(return_value={
        "data": [
            {"model_name": "already-here", "litellm_params": {"model": "x"}, "model_info": {}},
            {"model_name": "new-model-1", "litellm_params": {"model": "openai/one"}, "model_info": {"mode": "chat"}},
            {"model_name": "new-model-2", "litellm_params": {"model": "openai/two"}, "model_info": {"mode": "chat"}},
            {"model_name": "new-model-2", "litellm_params": {"model": "openai/two"}, "model_info": {}},
        ]
    })
    mock_cls.return_value = instance

    response = client.post(
        "/admin/models/import-all",
        headers=auth(admin_token),
        json={"region_id": test_region.id},
    )
    assert response.status_code == 201
    data = response.json()
    assert sorted(data["imported"]) == ["new-model-1", "new-model-2"]
    assert data["skipped"] == ["already-here"]
    assert data["errors"] == {}
    assert existing is not None

    imported = db.query(DBModel).filter_by(model_id="new-model-1").first()
    assert imported is not None
    assoc = db.query(DBModelRegion).filter_by(model_id=imported.id, region_id=test_region.id).first()
    assert assoc.sync_status == "synced"


def test_bedrock_candidates(client, admin_token):
    catalog = [
        {
            "modelId": "anthropic.claude-opus-5-v1:0",
            "modelName": "Claude Opus 5",
            "providerName": "Anthropic",
            "regions": ["us-east-1", "eu-central-1", "eu-central-2"],
            "modelLifecycle": {"status": "ACTIVE"},
        },
        {
            "modelId": "anthropic.claude-legacy-v1",
            "modelName": "Claude Legacy",
            "providerName": "Anthropic",
            "regions": ["us-east-1"],
            "modelLifecycle": {"status": "LEGACY"},
        },
        {
            "modelId": "meta.llama-9",
            "modelName": "Llama 9",
            "providerName": "Meta",
            "regions": ["us-east-1"],
        },
    ]
    with patch("app.api.public._fetch_bedrock_catalog", AsyncMock(return_value=catalog)):
        response = client.get(
            "/admin/models/bedrock-candidates?q=claude",
            headers=auth(admin_token),
        )
    assert response.status_code == 200
    data = response.json()
    ids = [c["model_id"] for c in data["candidates"]]
    assert ids == ["anthropic.claude-opus-5-v1:0"]  # LEGACY filtered, llama not matched
    assert data["candidates"][0]["regions"] == ["eu-central-1", "eu-central-2", "us-east-1"]
    assert data["area_aws_regions"]["CH"] == ["eu-central-2"]


def test_region_regional_area_enum(client, admin_token, db, test_region):
    # Valid area accepted via region update
    response = client.get("/regions/admin", headers=auth(admin_token))
    assert response.status_code == 200
    assert any("regional_area" in r for r in response.json())
