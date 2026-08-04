from unittest.mock import patch, AsyncMock, MagicMock
from app.db.models import DBModel, DBModelRegion


def test_non_admin_cannot_access_endpoints(client, test_token):
    """Test that regular users cannot access admin endpoints."""
    response = client.get(
        "/admin/models",
        headers={"Authorization": f"Bearer {test_token}"}
    )
    assert response.status_code in (401, 403)

    response = client.get(
        "/admin/models/1",
        headers={"Authorization": f"Bearer {test_token}"}
    )
    assert response.status_code in (401, 403)


def test_admin_get_and_list_models(client, admin_token, test_region, db):
    """Test retrieving and listing models as admin."""
    # Create two models in DB
    m1 = DBModel(
        model_id="anthropic/claude-3-opus",
        display_name="Claude 3 Opus",
        provider="anthropic",
        type="chat",
    )
    m2 = DBModel(
        model_id="meta/llama-3-70b",
        display_name="Llama 3 70B",
        provider="meta",
        type="chat",
    )
    db.add_all([m1, m2])
    db.commit()

    # List models
    response = client.get(
        "/admin/models",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 2

    # Check that regions are populated in the response
    model_ids = [m["model_id"] for m in data]
    assert "anthropic/claude-3-opus" in model_ids
    assert "meta/llama-3-70b" in model_ids

    # Retrieve single model by ID
    response = client.get(
        f"/admin/models/{m1.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["model_id"] == "anthropic/claude-3-opus"

    # Verify that the unassociated region defaults to 'not_configured'
    regions = res_data["regions"]
    assert len(regions) > 0
    test_reg_data = next(r for r in regions if r["region_id"] == test_region.id)
    assert test_reg_data["sync_status"] == "not_configured"
    assert test_reg_data["is_active"] is False

    # Retrieve single model by model_id string
    response = client.get(
        f"/admin/models/{m2.model_id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    assert response.json()["id"] == m2.id


def test_admin_models_are_read_only(client, admin_token):
    """The catalog is authored in amazeeai-model-catalog and applied via
    /admin/models/apply — direct CRUD must not exist."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    body = {"model_id": "x", "display_name": "x", "provider": "x", "type": "chat"}
    create_res = client.post("/admin/models", headers=headers, json=body)
    update_res = client.put("/admin/models/1", headers=headers, json=body)
    delete_res = client.delete("/admin/models/1", headers=headers)
    toggle_res = client.post("/admin/models/region-toggle", headers=headers, json={})
    assert create_res.status_code == 405
    assert update_res.status_code == 405
    assert delete_res.status_code == 405
    assert toggle_res.status_code == 405


def test_admin_list_models_search_wildcard_escaping(client, admin_token, db):
    """Test that LIKE wildcards (_ and %) are escaped properly in list search."""
    m1 = DBModel(
        model_id="test/model_1",
        display_name="Model with Underscore",
        provider="test",
        type="chat",
    )
    m2 = DBModel(
        model_id="test/model-1",
        display_name="Model with Hyphen",
        provider="test",
        type="chat",
    )
    db.add_all([m1, m2])
    db.commit()

    # Search for "model_1". Without escaping, "_" would match "-" (test/model-1).
    # With escaping, it must ONLY match "test/model_1" (Model with Underscore).
    response = client.get(
        "/admin/models?search=model_1",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    data = response.json()
    model_ids = [m["model_id"] for m in data]
    assert "test/model_1" in model_ids
    assert "test/model-1" not in model_ids


@patch("app.services.model_sync.LiteLLMService")
def test_sync_model_narrow_exception_handling_connection_refused(mock_litellm_class, db, test_region):
    """Test that sync task fails fast on connection refused and does not call update_model."""
    from app.services.model_sync import sync_model_to_region_task

    mock_instance = MagicMock()
    mock_instance.get_model_deployment_ids = AsyncMock(return_value=[])
    mock_instance.add_model = AsyncMock(side_effect=Exception("Connection refused"))
    mock_instance.update_model = AsyncMock()
    mock_litellm_class.return_value = mock_instance

    m = DBModel(
        model_id="test/failed-fast-sync",
        display_name="Failed Fast Sync",
        provider="test",
        type="chat",
    )
    db.add(m)
    db.commit()

    assoc = DBModelRegion(
        model_id=m.id,
        region_id=test_region.id,
        is_active=True,
        sync_status="pending",
    )
    db.add(assoc)
    db.commit()

    # Run the background task logic directly
    import asyncio
    asyncio.run(sync_model_to_region_task(m.id, test_region.id))

    db.refresh(assoc)
    assert assoc.sync_status == "failed"
    assert "Connection refused" in assoc.sync_error
    mock_instance.add_model.assert_called_once()
    mock_instance.update_model.assert_not_called()


@patch("app.services.model_sync.LiteLLMService")
def test_sync_model_existing_deployment_updates(mock_litellm_class, db, test_region):
    """Test that sync task updates (by deployment id) instead of adding when the
    model is already registered in the region's LiteLLM."""
    from app.services.model_sync import sync_model_to_region_task

    mock_instance = MagicMock()
    mock_instance.get_model_deployment_ids = AsyncMock(return_value=["dep-123"])
    mock_instance.add_model = AsyncMock()
    mock_instance.update_model = AsyncMock(return_value={"status": "success"})
    mock_litellm_class.return_value = mock_instance

    m = DBModel(
        model_id="test/conflict-sync",
        display_name="Conflict Sync",
        provider="test",
        type="chat",
    )
    db.add(m)
    db.commit()

    assoc = DBModelRegion(
        model_id=m.id,
        region_id=test_region.id,
        is_active=True,
        sync_status="pending",
    )
    db.add(assoc)
    db.commit()

    import asyncio
    asyncio.run(sync_model_to_region_task(m.id, test_region.id))

    db.refresh(assoc)
    assert assoc.sync_status == "synced"
    assert assoc.sync_error is None
    mock_instance.add_model.assert_not_called()
    mock_instance.update_model.assert_called_once_with(
        "test/conflict-sync", {}, ["dep-123"], access_groups=[]
    )


@patch("app.services.model_sync.LiteLLMService")
def test_sync_model_poisoned_session_never_commits_stale_synced(mock_litellm_class, db, test_region):
    """If a failure happens after sync_status='synced' is set in memory AND the
    error-path re-query also fails (poisoned session), the finally block must not
    commit the stale 'synced' state — the association stays 'pending' for retry."""
    from app.services import model_sync
    from app.services.model_sync import sync_model_to_region_task
    from app.db.database import get_db as real_get_db

    mock_instance = MagicMock()
    mock_instance.get_model_deployment_ids = AsyncMock(return_value=[])
    mock_instance.add_model = AsyncMock(return_value={"status": "success"})
    mock_litellm_class.return_value = mock_instance

    m = DBModel(
        model_id="test/poisoned-session",
        display_name="Poisoned Session",
        provider="test",
        type="chat",
    )
    db.add(m)
    db.commit()

    assoc = DBModelRegion(
        model_id=m.id,
        region_id=test_region.id,
        is_active=True,
        sync_status="pending",
    )
    db.add(assoc)
    db.commit()

    class PoisonedSession:
        """Delegates to a real session; the 6th query (the error-path re-query,
        after assoc/model/region/effective-params/access-group-slugs fetches)
        raises as if the connection dropped."""
        def __init__(self, real):
            self._real = real
            self._queries = 0

        def query(self, *args, **kwargs):
            self._queries += 1
            if self._queries > 5:
                raise RuntimeError("connection lost")
            return self._real.query(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._real, name)

    def fake_get_db():
        yield PoisonedSession(next(real_get_db()))

    import asyncio
    # Third logger.info call is "Successfully synchronized...", which runs after
    # sync_status='synced' is set in memory — raising there simulates a late failure.
    with patch("app.services.model_sync.get_db", fake_get_db), \
         patch.object(model_sync.logger, "info", side_effect=[None, None, RuntimeError("late failure")]):
        asyncio.run(sync_model_to_region_task(m.id, test_region.id))

    db.expire_all()
    db.refresh(assoc)
    assert assoc.sync_status == "pending"  # NOT 'synced' — stale state must not be committed
    # Pin the path: add_model must have run, proving the failure happened AFTER
    # 'synced' was set in memory (guards against logger call-order drift).
    mock_instance.add_model.assert_awaited_once()


@patch("app.services.model_sync.LiteLLMService")
def test_sync_model_global_deactivation_deregisters(mock_litellm_class, db, test_region):
    """Test that a globally inactive model with regionally active association deregistrates/deletes from LiteLLM."""
    from app.services.model_sync import sync_model_to_region_task

    mock_instance = MagicMock()
    mock_instance.get_model_deployment_ids = AsyncMock(return_value=["dep-1"])
    mock_instance.delete_model = AsyncMock(return_value=None)
    mock_litellm_class.return_value = mock_instance

    m = DBModel(
        model_id="test/globally-inactive-model",
        display_name="Globally Inactive Model",
        provider="test",
        type="chat",
        is_active_globally=False,  # Globally disabled!
    )
    db.add(m)
    db.commit()

    assoc = DBModelRegion(
        model_id=m.id,
        region_id=test_region.id,
        is_active=True,  # Regionally still active, but global deactivation takes precedence!
        sync_status="pending",
    )
    db.add(assoc)
    db.commit()

    import asyncio
    asyncio.run(sync_model_to_region_task(m.id, test_region.id))

    db.refresh(assoc)
    assert assoc.sync_status == "synced"
    assert assoc.sync_error is None
    mock_instance.delete_model.assert_called_once_with("test/globally-inactive-model", ["dep-1"])


def test_admin_get_model_redacts_credentials(client, admin_token, db):
    """Detail endpoint must redact credential values but keep keys and os.environ/ refs visible."""
    m = DBModel(
        model_id="redaction-test-model",
        display_name="Redaction Test",
        provider="openai",
        type="chat",
        litellm_params={
            "model": "gpt-4o",
            "api_key": "sk-super-secret",
            "azure_api_key": "os.environ/AZURE_API_KEY",
            "vertex_credentials": '{"private_key": "-----BEGIN PRIVATE KEY-----"}',
            "extra_headers": {"x-api-key": "hdr-secret", "x-trace": "abc"},
            "max_tokens": 8192,
            "temperature": 0.5,
        },
    )
    db.add(m)
    db.commit()

    response = client.get(
        f"/admin/models/{m.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    params = response.json()["litellm_params"]
    assert params["api_key"] == "********"
    assert params["azure_api_key"] == "os.environ/AZURE_API_KEY"
    assert params["vertex_credentials"] == "********"
    assert params["extra_headers"]["x-api-key"] == "********"
    assert params["extra_headers"]["x-trace"] == "abc"
    assert params["max_tokens"] == 8192  # numeric values are never redacted
    assert params["model"] == "gpt-4o"
    assert params["temperature"] == 0.5
    assert "sk-super-secret" not in response.text
    assert "hdr-secret" not in response.text
    assert "PRIVATE KEY" not in response.text
