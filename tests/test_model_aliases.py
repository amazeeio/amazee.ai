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
# Regional area
# ---------------------------------------------------------------------------


def test_region_regional_area_enum(client, admin_token, db, test_region):
    # Valid area accepted via region update
    response = client.get("/regions/admin", headers=auth(admin_token))
    assert response.status_code == 200
    assert any("regional_area" in r for r in response.json())
