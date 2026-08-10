from unittest.mock import patch, AsyncMock

from app.db.models import (
    DBModel,
    DBModelAccessGroup,
    DBModelAccessGroupModel,
    DBModelAccessGroupRegion,
    DBModelRegion,
    DBRegion,
    DBTeamModelAccessGroup,
)
from app.services.access_groups import (
    effective_team_group_slugs,
    model_access_group_slugs,
)


def _make_model(db, model_id="openai/test-model", **kwargs):
    model = DBModel(
        model_id=model_id,
        display_name=kwargs.get("display_name", model_id),
        provider=kwargs.get("provider", "openai"),
        type=kwargs.get("type", "chat"),
        is_active_globally=kwargs.get("is_active_globally", True),
        litellm_params={"model": model_id},
    )
    db.add(model)
    db.commit()
    db.refresh(model)
    return model


def _make_group(db, slug="default-zdr", model_ids=(), region_ids=()):
    group = DBModelAccessGroup(slug=slug, label=slug.title(), description=None)
    db.add(group)
    db.flush()
    for model_id in model_ids:
        db.add(DBModelAccessGroupModel(group_id=group.id, model_id=model_id))
    for region_id in region_ids:
        db.add(DBModelAccessGroupRegion(group_id=group.id, region_id=region_id))
    db.commit()
    db.refresh(group)
    return group


def _deploy_model(db, model, region, sync_status="synced"):
    assoc = DBModelRegion(
        model_id=model.id, region_id=region.id, is_active=True, sync_status=sync_status
    )
    db.add(assoc)
    db.commit()
    return assoc


def auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Group CRUD
# ---------------------------------------------------------------------------


def test_create_and_get_access_group(client, admin_token, db, test_region):
    model = _make_model(db)
    with patch("app.api.access_groups.sync_model_to_region_task"):
        response = client.post(
            "/admin/access-groups",
            headers=auth(admin_token),
            json={
                "slug": "default-zdr",
                "label": "Default ZDR Models",
                "description": "Zero data retention",
                "model_ids": [model.id],
                "region_ids": [test_region.id],
            },
        )
    assert response.status_code == 201
    data = response.json()
    assert data["slug"] == "default-zdr"
    assert data["model_ids"] == [model.id]
    assert data["region_ids"] == [test_region.id]
    assert data["default_in_region_ids"] == []
    assert data["team_count"] == 0

    listing = client.get("/admin/access-groups", headers=auth(admin_token))
    assert listing.status_code == 200
    assert [g["slug"] for g in listing.json()] == ["default-zdr"]


def test_create_access_group_duplicate_slug(client, admin_token, db):
    _make_group(db, slug="default-zdr")
    response = client.post(
        "/admin/access-groups",
        headers=auth(admin_token),
        json={"slug": "default-zdr", "label": "Duplicate"},
    )
    assert response.status_code == 400


def test_create_access_group_invalid_slug(client, admin_token):
    response = client.post(
        "/admin/access-groups",
        headers=auth(admin_token),
        json={"slug": "Not A Slug!", "label": "Bad"},
    )
    assert response.status_code == 422


def test_create_access_group_unknown_model(client, admin_token):
    response = client.post(
        "/admin/access-groups",
        headers=auth(admin_token),
        json={"slug": "grp", "label": "G", "model_ids": [999999]},
    )
    assert response.status_code == 400


def test_update_access_group_membership_triggers_resync(
    client, admin_token, db, test_region
):
    model_a = _make_model(db, "openai/model-a")
    model_b = _make_model(db, "openai/model-b")
    _deploy_model(db, model_a, test_region)
    _deploy_model(db, model_b, test_region)
    group = _make_group(
        db, slug="grp", model_ids=[model_a.id], region_ids=[test_region.id]
    )

    with patch("app.api.access_groups.sync_model_to_region_task") as mock_sync:
        response = client.put(
            f"/admin/access-groups/{group.id}",
            headers=auth(admin_token),
            json={"label": "Renamed Label", "model_ids": [model_b.id]},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["label"] == "Renamed Label"
    assert data["slug"] == "grp"  # slug is immutable (not even accepted)
    assert data["model_ids"] == [model_b.id]
    # Both the removed and the added model get re-synced (tags change on both)
    synced_models = {call.args[0] for call in mock_sync.call_args_list}
    assert synced_models == {model_a.id, model_b.id}


def test_undeploy_region_blocked_while_default(client, admin_token, db, test_region):
    group = _make_group(db, slug="grp", region_ids=[test_region.id])
    test_region.default_access_group_id = group.id
    db.commit()

    response = client.put(
        f"/admin/access-groups/{group.id}",
        headers=auth(admin_token),
        json={"region_ids": []},
    )
    assert response.status_code == 409


def test_delete_access_group_blocked_while_default(client, admin_token, db, test_region):
    group = _make_group(db, slug="grp", region_ids=[test_region.id])
    test_region.default_access_group_id = group.id
    db.commit()

    response = client.delete(
        f"/admin/access-groups/{group.id}", headers=auth(admin_token)
    )
    assert response.status_code == 409


def test_delete_access_group_detaches_teams_and_untags_models(
    client, admin_token, db, test_region, test_team
):
    model = _make_model(db)
    _deploy_model(db, model, test_region)
    group = _make_group(
        db, slug="grp", model_ids=[model.id], region_ids=[test_region.id]
    )
    db.add(DBTeamModelAccessGroup(team_id=test_team.id, group_id=group.id))
    db.commit()

    with (
        patch("app.api.access_groups.sync_model_to_region_task") as mock_model_sync,
        patch("app.api.access_groups.sync_team_groups_task"),
    ):
        response = client.delete(
            f"/admin/access-groups/{group.id}", headers=auth(admin_token)
        )
    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "models_untagged": 1,
        "teams_detached": 1,
    }
    assert db.query(DBModelAccessGroup).count() == 0
    assert db.query(DBTeamModelAccessGroup).count() == 0
    assert mock_model_sync.call_args_list[0].args[0] == model.id


# ---------------------------------------------------------------------------
# Region default (the enforcement switch)
# ---------------------------------------------------------------------------


def test_set_region_default_rejects_undeployed_group(
    client, admin_token, db, test_region
):
    group = _make_group(db, slug="grp")
    response = client.put(
        f"/admin/regions/{test_region.id}/default-access-group",
        headers=auth(admin_token),
        json={"group_id": group.id},
    )
    assert response.status_code == 409
    assert "not deployed" in response.json()["detail"]


def test_set_region_default_rejects_empty_group(client, admin_token, db, test_region):
    group = _make_group(db, slug="grp", region_ids=[test_region.id])
    response = client.put(
        f"/admin/regions/{test_region.id}/default-access-group",
        headers=auth(admin_token),
        json={"group_id": group.id},
    )
    assert response.status_code == 409
    assert "no active models" in response.json()["detail"]


def test_set_region_default_rejects_unsynced_models(
    client, admin_token, db, test_region
):
    model = _make_model(db)
    _deploy_model(db, model, test_region, sync_status="pending")
    group = _make_group(
        db, slug="grp", model_ids=[model.id], region_ids=[test_region.id]
    )
    response = client.put(
        f"/admin/regions/{test_region.id}/default-access-group",
        headers=auth(admin_token),
        json={"group_id": group.id},
    )
    assert response.status_code == 409
    assert model.model_id in response.json()["detail"]


def test_set_and_clear_region_default_starts_fanout(
    client, admin_token, db, test_region
):
    model = _make_model(db)
    _deploy_model(db, model, test_region, sync_status="synced")
    group = _make_group(
        db, slug="grp", model_ids=[model.id], region_ids=[test_region.id]
    )

    with patch("app.api.access_groups.sync_region_teams_task") as mock_fanout:
        response = client.put(
            f"/admin/regions/{test_region.id}/default-access-group",
            headers=auth(admin_token),
            json={"group_id": group.id},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["run_id"] is not None
    mock_fanout.assert_called_once_with(body["run_id"])
    db.refresh(test_region)
    assert test_region.default_access_group_id == group.id

    run = client.get(
        f"/admin/regions/{test_region.id}/team-group-sync-run",
        headers=auth(admin_token),
    )
    assert run.status_code == 200
    assert run.json()["id"] == body["run_id"]

    # Setting the same value again is a no-op
    with patch("app.api.access_groups.sync_region_teams_task") as mock_fanout:
        response = client.put(
            f"/admin/regions/{test_region.id}/default-access-group",
            headers=auth(admin_token),
            json={"group_id": group.id},
        )
    assert response.json()["status"] == "unchanged"
    mock_fanout.assert_not_called()

    # Clearing (enforcement off) also fans out, to roll teams back
    with patch("app.api.access_groups.sync_region_teams_task") as mock_fanout:
        response = client.put(
            f"/admin/regions/{test_region.id}/default-access-group",
            headers=auth(admin_token),
            json={"group_id": None},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    mock_fanout.assert_called_once()
    db.refresh(test_region)
    assert test_region.default_access_group_id is None


# ---------------------------------------------------------------------------
# Team opt-ins (the MOAD endpoint)
# ---------------------------------------------------------------------------


def test_set_team_access_groups_rejected_without_enforcement(
    client, admin_token, db, test_region, test_team
):
    _make_group(db, slug="extended", region_ids=[test_region.id])
    response = client.put(
        f"/admin/teams/{test_team.id}/access-groups",
        headers=auth(admin_token),
        json={"access_groups": ["extended"]},
    )
    assert response.status_code == 409


def test_set_team_access_groups_success(client, admin_token, db, test_region, test_team):
    default_group = _make_group(db, slug="default-zdr", region_ids=[test_region.id])
    extended = _make_group(db, slug="extended", region_ids=[test_region.id])
    test_region.default_access_group_id = default_group.id
    # test_region's fixture only associates teams that existed before it ran
    test_team.region_id = test_region.id
    db.commit()

    with patch("app.api.access_groups.sync_team_groups_task") as mock_sync:
        response = client.put(
            f"/admin/teams/{test_team.id}/access-groups",
            headers=auth(admin_token),
            json={"access_groups": ["extended"]},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["access_groups"] == ["extended"]
    assert data["defaults"] == {test_region.name: "default-zdr"}
    mock_sync.assert_called_once_with(test_team.id, test_region.id)

    # Unknown slug rejected, replace semantics verified
    response = client.put(
        f"/admin/teams/{test_team.id}/access-groups",
        headers=auth(admin_token),
        json={"access_groups": ["nope"]},
    )
    assert response.status_code == 400

    with patch("app.api.access_groups.sync_team_groups_task"):
        response = client.put(
            f"/admin/teams/{test_team.id}/access-groups",
            headers=auth(admin_token),
            json={"access_groups": []},
        )
    assert response.status_code == 200
    assert response.json()["access_groups"] == []
    assert db.query(DBTeamModelAccessGroup).filter_by(team_id=test_team.id).count() == 0
    assert extended is not None


def test_get_team_access_groups(client, admin_token, db, test_region, test_team):
    group = _make_group(db, slug="extended", region_ids=[test_region.id])
    db.add(DBTeamModelAccessGroup(team_id=test_team.id, group_id=group.id))
    db.commit()
    response = client.get(
        f"/admin/teams/{test_team.id}/access-groups", headers=auth(admin_token)
    )
    assert response.status_code == 200
    assert response.json()["access_groups"] == ["extended"]


def test_access_groups_require_admin(client, test_token):
    response = client.get("/admin/access-groups", headers=auth(test_token))
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Slug computation + model sync integration
# ---------------------------------------------------------------------------


def test_effective_team_group_slugs(db, test_region, test_team):
    other_region = DBRegion(
        name="other-region",
        litellm_api_url="https://other-litellm.com",
        litellm_api_key="key",
        is_active=True,
    )
    db.add(other_region)
    db.commit()

    default_group = _make_group(db, slug="default-zdr", region_ids=[test_region.id])
    deployed_opt_in = _make_group(db, slug="extended", region_ids=[test_region.id])
    undeployed_opt_in = _make_group(db, slug="elsewhere", region_ids=[other_region.id])
    db.add(DBTeamModelAccessGroup(team_id=test_team.id, group_id=deployed_opt_in.id))
    db.add(DBTeamModelAccessGroup(team_id=test_team.id, group_id=undeployed_opt_in.id))
    db.commit()

    # Enforcement off -> None (leave team untouched / clear)
    assert effective_team_group_slugs(db, test_team.id, test_region) is None

    test_region.default_access_group_id = default_group.id
    db.commit()
    # Default + opt-ins deployed to this region only
    assert effective_team_group_slugs(db, test_team.id, test_region) == [
        "default-zdr",
        "extended",
    ]


def test_model_access_group_slugs(db, test_region, test_team):
    model = _make_model(db)
    _make_group(db, slug="in-region", model_ids=[model.id], region_ids=[test_region.id])
    _make_group(db, slug="not-deployed", model_ids=[model.id])
    assert model_access_group_slugs(db, model.id, test_region.id) == ["in-region"]


def test_public_listing_does_not_enforce_on_unmanaged_regions(db, test_region, monkeypatch):
    """Model->group membership is global (no region column), so a catalog apply
    rewrites it for every region at once. A region the catalog does not manage
    must therefore not filter its listing on it — its teams are unrestricted on
    LiteLLM anyway (see effective_team_group_slugs)."""
    from app.api.public import _filter_region_groups_by_access
    from app.core.config import settings
    from app.schemas.models import (
        PublicModelCapabilities,
        PublicModelPricing,
        PublicModelSummary,
        PublicRegionModels,
    )

    group = _make_group(db, slug="default-zdr", region_ids=[test_region.id])
    test_region.default_access_group_id = group.id
    db.commit()
    groups = [
        PublicRegionModels(
            region=test_region.name,
            status="available",
            models=[
                PublicModelSummary(
                    model_id="openai/not-in-any-group",
                    display_name="Not In Any Group",
                    provider="openai",
                    type="chat",
                    description="",
                    capabilities=PublicModelCapabilities(),
                    pricing=PublicModelPricing(),
                )
            ],
        )
    ]

    monkeypatch.setattr(settings, "ENV_SUFFIX", "production")
    monkeypatch.setattr(settings, "CATALOG_MANAGED_REGIONS", test_region.name)
    assert _filter_region_groups_by_access(db, groups, None)[0].models == []

    monkeypatch.setattr(settings, "CATALOG_MANAGED_REGIONS", "")
    assert _filter_region_groups_by_access(db, groups, None)[0].models == groups[0].models


@patch("app.services.model_sync.LiteLLMService")
def test_model_sync_pushes_access_groups(mock_service_cls, client, db, test_region):
    from app.services.model_sync import sync_model_to_region_task
    import asyncio

    model = _make_model(db)
    _deploy_model(db, model, test_region, sync_status="pending")
    _make_group(db, slug="grp", model_ids=[model.id], region_ids=[test_region.id])
    # The sync task commits and closes the session, detaching/expiring the
    # fixtures' instances — capture plain ids up front.
    model_pk, region_pk, model_key = model.id, test_region.id, model.model_id

    instance = mock_service_cls.return_value
    instance.get_model_deployment_ids = AsyncMock(return_value=[])
    instance.add_model = AsyncMock(return_value={})
    instance.list_access_groups = AsyncMock(return_value=[])
    instance.create_access_group = AsyncMock(return_value={})

    with patch("app.services.model_sync.get_db", lambda: iter([db])):
        asyncio.run(sync_model_to_region_task(model_pk, region_pk))

    instance.add_model.assert_called_once()
    assert instance.add_model.call_args.kwargs["access_groups"] == ["grp"]
    refreshed = (
        db.query(DBModelRegion)
        .filter_by(model_id=model_pk, region_id=region_pk)
        .first()
    )
    assert refreshed.sync_status == "synced"
    # The entity registry (what the LiteLLM UI lists) is reconciled too.
    from app.services.access_groups import ACCESS_GROUP_MANAGED_DESCRIPTION

    instance.create_access_group.assert_called_once_with(
        "grp", [model_key], description=ACCESS_GROUP_MANAGED_DESCRIPTION
    )


@patch("app.services.model_sync.LiteLLMService")
def test_model_sync_reconciles_access_group_entities(mock_service_cls, client, db, test_region):
    """Drifted member lists are corrected, stale managed entities are deleted,
    and hand-made entities (no marker description) are never touched."""
    from app.services.access_groups import ACCESS_GROUP_MANAGED_DESCRIPTION
    from app.services.model_sync import sync_model_to_region_task
    import asyncio

    model = _make_model(db)
    _deploy_model(db, model, test_region, sync_status="pending")
    _make_group(db, slug="grp", model_ids=[model.id], region_ids=[test_region.id])
    model_pk, region_pk = model.id, test_region.id
    model_key = model.model_id

    instance = mock_service_cls.return_value
    instance.get_model_deployment_ids = AsyncMock(return_value=[])
    instance.add_model = AsyncMock(return_value={})
    instance.list_access_groups = AsyncMock(
        return_value=[
            {
                "access_group_id": "ag-1",
                "access_group_name": "grp",
                "description": ACCESS_GROUP_MANAGED_DESCRIPTION,
                "access_model_names": ["stale-model"],
            },
            {
                "access_group_id": "ag-2",
                "access_group_name": "retired-group",
                "description": ACCESS_GROUP_MANAGED_DESCRIPTION,
                "access_model_names": [],
            },
            {
                "access_group_id": "ag-3",
                "access_group_name": "hand-made",
                "description": "made in the UI",
                "access_model_names": [],
            },
        ]
    )
    instance.update_access_group = AsyncMock(return_value={})
    instance.delete_access_group = AsyncMock(return_value=None)

    with patch("app.services.model_sync.get_db", lambda: iter([db])):
        asyncio.run(sync_model_to_region_task(model_pk, region_pk))

    instance.update_access_group.assert_called_once_with("ag-1", model_names=[model_key])
    instance.delete_access_group.assert_called_once_with("ag-2")
