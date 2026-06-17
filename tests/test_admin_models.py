from datetime import datetime, UTC, timedelta
from unittest.mock import patch, AsyncMock, MagicMock
from app.db.models import DBModel, DBModelRegion

def test_admin_create_model_success(client, admin_token, db):
    """Test creating a model as a system administrator."""
    real_eol_dt = datetime.now(UTC) + timedelta(days=365)
    override_eol_dt = datetime.now(UTC) + timedelta(days=180)
    
    payload = {
        "model_id": "openai/gpt-4o-test",
        "display_name": "GPT-4o Test",
        "provider": "openai",
        "type": "chat",
        "context_length": 128000,
        "max_output_tokens": 4096,
        "description": "Premium open AI test model",
        "real_eol": real_eol_dt.isoformat(),
        "override_eol": override_eol_dt.isoformat(),
        "is_active_globally": True,
        "litellm_params": {"temperature": 0.7, "top_p": 0.9}
    }
    
    response = client.post(
        "/admin/models",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=payload
    )
    
    assert response.status_code == 201
    data = response.json()
    assert data["model_id"] == "openai/gpt-4o-test"
    assert data["display_name"] == "GPT-4o Test"
    assert data["provider"] == "openai"
    assert data["type"] == "chat"
    assert data["context_length"] == 128000
    assert data["max_output_tokens"] == 4096
    assert data["description"] == "Premium open AI test model"
    assert data["is_active_globally"] is True
    assert data["litellm_params"] is None
    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data
    assert data["deleted_at"] is None

    # Retrieve via GET to verify unmasked params are returned
    get_res = client.get(
        f"/admin/models/{data['id']}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert get_res.status_code == 200
    get_data = get_res.json()
    assert get_data["litellm_params"] == {"temperature": 0.7, "top_p": 0.9}


def test_admin_create_model_date_validation_error(client, admin_token):
    """Test creating a model where override_eol is after real_eol returns 400."""
    real_eol_dt = datetime.now(UTC) + timedelta(days=100)
    override_eol_dt = datetime.now(UTC) + timedelta(days=150) # Invalid: override is after real
    
    payload = {
        "model_id": "openai/gpt-bad-dates",
        "display_name": "GPT Bad Dates",
        "provider": "openai",
        "type": "chat",
        "real_eol": real_eol_dt.isoformat(),
        "override_eol": override_eol_dt.isoformat()
    }
    
    response = client.post(
        "/admin/models",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=payload
    )
    
    assert response.status_code == 400
    assert "Override EOL cannot be set after Real EOL date" in response.json()["detail"]


def test_non_admin_cannot_access_endpoints(client, test_token):
    """Test that regular users cannot access admin endpoints."""
    # List models
    response = client.get(
        "/admin/models",
        headers={"Authorization": f"Bearer {test_token}"}
    )
    assert response.status_code in (401, 403)

    # Create model
    response = client.post(
        "/admin/models",
        headers={"Authorization": f"Bearer {test_token}"},
        json={"model_id": "test", "display_name": "test", "provider": "test", "type": "test"}
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


def test_admin_update_model(client, admin_token, db):
    """Test updating a model as admin."""
    m = DBModel(
        model_id="google/gemini-pro-test",
        display_name="Gemini Pro",
        provider="google",
        type="chat",
        is_active_globally=True,
    )
    db.add(m)
    db.commit()
    
    real_eol_dt = datetime.now(UTC) + timedelta(days=200)
    override_eol_dt = datetime.now(UTC) + timedelta(days=100)
    
    payload = {
        "display_name": "Gemini 1.5 Pro",
        "real_eol": real_eol_dt.isoformat(),
        "override_eol": override_eol_dt.isoformat(),
        "is_active_globally": False,
        "litellm_params": {"max_tokens": 8192}
    }
    
    response = client.put(
        f"/admin/models/{m.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=payload
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["display_name"] == "Gemini 1.5 Pro"
    assert data["is_active_globally"] is False
    assert data["litellm_params"] is None

    # Retrieve via GET to verify unmasked params are returned
    get_res = client.get(
        f"/admin/models/{m.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert get_res.status_code == 200
    get_data = get_res.json()
    assert get_data["litellm_params"] == {"max_tokens": 8192}

    # Test update with invalid dates
    bad_payload = {
        "override_eol": (real_eol_dt + timedelta(days=10)).isoformat()
    }
    response = client.put(
        f"/admin/models/{m.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=bad_payload
    )
    assert response.status_code == 400


def test_admin_soft_delete_model(client, admin_token, db):
    """Test soft deleting a model as admin."""
    m = DBModel(
        model_id="test/to-be-deleted",
        display_name="To Be Deleted",
        provider="test",
        type="chat",
    )
    db.add(m)
    db.commit()
    
    # Delete model
    response = client.delete(
        f"/admin/models/{m.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    assert response.json()["deleted_at"] is not None
    
    # Retrieve it again (should fail since CRUD operates on non-deleted by default)
    response = client.get(
        f"/admin/models/{m.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 404

    # Retrieve with include_deleted=True directly (should succeed)
    response_deleted = client.get(
        f"/admin/models/{m.id}?include_deleted=true",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response_deleted.status_code == 200
    assert response_deleted.json()["id"] == m.id
    
    # Let's verify get_model returns it, but list_models (by default) does not.
    response_list = client.get(
        "/admin/models",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    model_ids = [x["id"] for x in response_list.json()]
    assert m.id not in model_ids

    # List with include_deleted=True
    response_list_all = client.get(
        "/admin/models?include_deleted=true",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    model_ids_all = [x["id"] for x in response_list_all.json()]
    assert m.id in model_ids_all


@patch("app.services.model_sync.LiteLLMService")
def test_admin_toggle_model_region_success(mock_litellm_class, client, admin_token, test_region, db):
    """Test toggling model status per region (successful sync)."""
    # Set up mock instances
    mock_instance = MagicMock()
    mock_instance.add_model = AsyncMock(return_value={"status": "success"})
    mock_instance.delete_model = AsyncMock(return_value=None)
    mock_litellm_class.return_value = mock_instance

    m = DBModel(
        model_id="openai/gpt-3.5-turbo",
        display_name="GPT-3.5 Turbo",
        provider="openai",
        type="chat",
    )
    db.add(m)
    db.commit()
    
    # Toggle region ON
    payload = {
        "model_id": m.id,
        "region_id": test_region.id,
        "is_active": True
    }
    
    response = client.post(
        "/admin/models/region-toggle",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=payload
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    
    # Verify association exists in database with status 'synced'
    assoc = db.query(DBModelRegion).filter_by(model_id=m.id, region_id=test_region.id).first()
    assert assoc is not None
    assert assoc.is_active is True
    assert assoc.sync_status == "synced"
    mock_instance.add_model.assert_called_once_with("openai/gpt-3.5-turbo", None)
    
    # Toggle region OFF
    payload["is_active"] = False
    response = client.post(
        "/admin/models/region-toggle",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=payload
    )
    assert response.status_code == 200
    
    db.refresh(assoc)
    assert assoc.is_active is False
    assert assoc.sync_status == "synced"
    mock_instance.delete_model.assert_called_once_with("openai/gpt-3.5-turbo")


@patch("app.services.model_sync.LiteLLMService")
def test_admin_toggle_model_region_failure(mock_litellm_class, client, admin_token, test_region, db):
    """Test toggling model status per region (failed sync)."""
    # Set up mock instances
    mock_instance = MagicMock()
    mock_instance.add_model = AsyncMock(side_effect=Exception("Connection refused"))
    mock_instance.update_model = AsyncMock(side_effect=Exception("Connection refused retry failed"))
    mock_litellm_class.return_value = mock_instance

    m = DBModel(
        model_id="openai/gpt-3.5-turbo-fail",
        display_name="GPT-3.5 Turbo Failure",
        provider="openai",
        type="chat",
    )
    db.add(m)
    db.commit()
    
    # Toggle region ON
    payload = {
        "model_id": m.id,
        "region_id": test_region.id,
        "is_active": True
    }
    
    response = client.post(
        "/admin/models/region-toggle",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=payload
    )
    assert response.status_code == 200
    
    # Verify association exists in database with status 'failed'
    assoc = db.query(DBModelRegion).filter_by(model_id=m.id, region_id=test_region.id).first()
    assert assoc is not None
    assert assoc.is_active is True
    assert assoc.sync_status == "failed"
    assert "Connection refused" in assoc.sync_error


@patch("app.services.model_sync.LiteLLMService")
def test_admin_delete_model_queues_failed_inactive_association(mock_litellm_class, client, admin_token, test_region, db):
    """Test that soft-deleting a model triggers deregistration sync for inactive but previously failed associations."""
    # Set up mock instances
    mock_instance = MagicMock()
    mock_instance.delete_model = AsyncMock(return_value=None)
    mock_litellm_class.return_value = mock_instance

    m = DBModel(
        model_id="test/failed-inactive-dereg",
        display_name="Failed Inactive Dereg",
        provider="test",
        type="chat",
    )
    db.add(m)
    db.commit()

    # Create an association that is inactive but failed previously (is_active=False, sync_status="failed")
    assoc = DBModelRegion(
        model_id=m.id,
        region_id=test_region.id,
        is_active=False,
        sync_status="failed",
        sync_error="Previous deregistration failed"
    )
    db.add(assoc)
    db.commit()

    # Soft-delete the model
    response = client.delete(
        f"/admin/models/{m.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200

    # Verify that background task was triggered and successfully processed
    db.refresh(assoc)
    assert assoc.sync_status == "synced"
    assert assoc.sync_error is None
    mock_instance.delete_model.assert_called_once_with("test/failed-inactive-dereg")


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


def test_admin_recreate_soft_deleted_model(client, admin_token, db):
    """Test that a soft-deleted model_id can be recycled and recreated."""
    model_id = "test/recycled-model"
    payload = {
        "model_id": model_id,
        "display_name": "Recycled Model",
        "provider": "test",
        "type": "chat",
    }

    # 1. Create first time
    r1 = client.post(
        "/admin/models",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=payload
    )
    assert r1.status_code == 201
    model_id_db = r1.json()["id"]

    # 2. Try to create again while active -> should fail with 400
    r2 = client.post(
        "/admin/models",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=payload
    )
    assert r2.status_code == 400
    assert "already exists" in r2.json()["detail"]

    # 3. Soft-delete the active model
    r3 = client.delete(
        f"/admin/models/{model_id_db}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r3.status_code == 200

    # 4. Re-create the model with same model_id -> should succeed (recycling)
    r4 = client.post(
        "/admin/models",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=payload
    )
    assert r4.status_code == 201
    assert r4.json()["id"] != model_id_db


@patch("app.services.model_sync.LiteLLMService")
def test_sync_model_narrow_exception_handling_connection_refused(mock_litellm_class, db, test_region):
    """Test that sync task fails fast on connection refused and does not call update_model."""
    from app.services.model_sync import sync_model_to_region_task

    mock_instance = MagicMock()
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
def test_sync_model_narrow_exception_handling_already_exists(mock_litellm_class, db, test_region):
    """Test that sync task retries with update_model if add_model fails indicating already exists."""
    from app.services.model_sync import sync_model_to_region_task

    mock_instance = MagicMock()
    mock_instance.add_model = AsyncMock(side_effect=Exception("Conflict: already registered"))
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
    mock_instance.add_model.assert_called_once()
    mock_instance.update_model.assert_called_once()


@patch("app.services.model_sync.LiteLLMService")
def test_sync_model_global_deactivation_deregisters(mock_litellm_class, db, test_region):
    """Test that a globally inactive model with regionally active association deregistrates/deletes from LiteLLM."""
    from app.services.model_sync import sync_model_to_region_task

    mock_instance = MagicMock()
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
    mock_instance.delete_model.assert_called_once_with("test/globally-inactive-model")


def test_admin_update_model_global_deactivation_queues_sync(client, admin_token, test_region, db):
    """Test that PUT /admin/models/{id} queues sync tasks only if global activation changes or is active."""
    m = DBModel(
        model_id="test/transition-model",
        display_name="Transition Model",
        provider="test",
        type="chat",
        is_active_globally=True,
    )
    db.add(m)
    db.commit()

    assoc = DBModelRegion(
        model_id=m.id,
        region_id=test_region.id,
        is_active=True,
        sync_status="synced",
    )
    db.add(assoc)
    db.commit()

    # 1. Update to is_active_globally=False -> should queue a sync task to deregister!
    with patch("app.api.admin_models.sync_model_to_region_task") as mock_sync_task:
        response = client.put(
            f"/admin/models/{m.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"is_active_globally": False}
        )
        assert response.status_code == 200
        db.refresh(assoc)
        assert assoc.sync_status == "pending"
        # Since it runs via background tasks in FastAPI, let's verify if mock was called or queued.
        # Actually FastAPI's BackgroundTasks are executed after response in TestClient.
        # So we can just check if mock_sync_task was called in the background.
        # (FastAPI background tasks are executed synchronously by the TestClient at the end of the request)
        assert mock_sync_task.called

    # 2. Update when is_active_globally is already False -> should NOT queue a sync task!
    # Let's reset assoc status to "synced" manually first to see if it remains synced (not modified to "pending")
    assoc.sync_status = "synced"
    db.commit()

    with patch("app.api.admin_models.sync_model_to_region_task") as mock_sync_task_2:
        response = client.put(
            f"/admin/models/{m.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"display_name": "New Name"}  # is_active_globally remains False
        )
        assert response.status_code == 200
        db.refresh(assoc)
        # Should remain synced because no sync was queued
        assert assoc.sync_status == "synced"
        mock_sync_task_2.assert_not_called()


def test_admin_toggle_model_region_globally_inactive_rejection(client, admin_token, test_region, db):
    """Test that toggling a region active on a globally inactive model fails with 400."""
    m = DBModel(
        model_id="test/globally-inactive-toggle",
        display_name="Globally Inactive Toggle",
        provider="test",
        type="chat",
        is_active_globally=False,  # Globally inactive!
    )
    db.add(m)
    db.commit()
    
    payload = {
        "model_id": m.id,
        "region_id": test_region.id,
        "is_active": True
    }
    
    response = client.post(
        "/admin/models/region-toggle",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=payload
    )
    assert response.status_code == 400
    assert "Cannot enable a region for a model that is globally inactive" in response.json()["detail"]
    
    # Verify that we CAN toggle region OFF (is_active=False) even if globally inactive (e.g. to cleanup/deregister)
    payload["is_active"] = False
    with patch("app.api.admin_models.sync_model_to_region_task"):
        response_off = client.post(
            "/admin/models/region-toggle",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=payload
        )
        assert response_off.status_code == 200


def test_admin_update_model_metadata_does_not_queue_sync(client, admin_token, test_region, db):
    """Test that PUT /admin/models/{id} does not queue sync if only metadata changes, but does if litellm_params changes."""
    m = DBModel(
        model_id="test/metadata-sync-test",
        display_name="Metadata Sync Test",
        provider="test",
        type="chat",
        is_active_globally=True,
    )
    db.add(m)
    db.commit()

    assoc = DBModelRegion(
        model_id=m.id,
        region_id=test_region.id,
        is_active=True,
        sync_status="synced",
    )
    db.add(assoc)
    db.commit()

    # 1. Update only display_name (metadata-only update for globally-active model)
    # This should NOT trigger a re-sync!
    with patch("app.api.admin_models.sync_model_to_region_task") as mock_sync_task:
        response = client.put(
            f"/admin/models/{m.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"display_name": "Metadata Sync Test Updated"}
        )
        assert response.status_code == 200
        db.refresh(assoc)
        assert assoc.sync_status == "synced"  # should remain synced
        mock_sync_task.assert_not_called()

    # 2. Update litellm_params (LiteLLM-relevant change)
    # This SHOULD trigger a re-sync!
    with patch("app.api.admin_models.sync_model_to_region_task") as mock_sync_task_2:
        response = client.put(
            f"/admin/models/{m.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"litellm_params": {"temperature": 0.5}}
        )
        assert response.status_code == 200
        db.refresh(assoc)
        assert assoc.sync_status == "pending"  # should be marked pending for re-sync
        assert mock_sync_task_2.called


@patch("app.services.litellm.LiteLLMService")
def test_admin_list_importable_models(mock_litellm_class, client, admin_token, test_region, db):
    """Test retrieving models currently configured in LiteLLM proxy but not in DB."""
    mock_instance = MagicMock()
    mock_instance.get_model_info = AsyncMock(return_value={
        "data": [
            {
                "model_name": "azure/gpt-4o-existing",
                "litellm_params": {
                    "model": "azure/gpt-4o",
                    "api_key": "os.environ/AZURE_API_KEY",
                    "api_base": "https://custom-azure-endpoint.openai.azure.com/"
                },
                "model_info": {
                    "litellm_provider": "azure",
                    "mode": "chat",
                    "max_input_tokens": 128000,
                    "max_output_tokens": 4096,
                    "metadata": "An existing Azure GPT-4o model"
                }
            }
        ]
    })
    mock_litellm_class.return_value = mock_instance

    # Query the /importable endpoint
    response = client.get(
        f"/admin/models/importable?region_id={test_region.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    model = data[0]
    assert model["model_id"] == "azure/gpt-4o-existing"
    assert model["display_name"] == "Azure/Gpt 4O Existing"
    assert model["provider"] == "azure"
    assert model["type"] == "chat"
    assert model["context_length"] == 128000
    assert model["max_output_tokens"] == 4096
    assert model["credential_keys"] == ["api_key"]


def test_admin_import_model_success(client, admin_token, test_region, db):
    """Test importing a model from regional LiteLLM instance."""
    payload = {
        "model_id": "azure/gpt-4o-existing",
        "display_name": "Azure GPT-4o Existing",
        "provider": "azure",
        "type": "chat",
        "context_length": 128000,
        "max_output_tokens": 4096,
        "description": "An existing Azure GPT-4o model",
        "is_active_globally": True,
        "litellm_params": {
            "model": "azure/gpt-4o",
            "api_key": "os.environ/AZURE_API_KEY",
            "api_base": "https://custom-azure-endpoint.openai.azure.com/"
        },
        "region_id": test_region.id
    }

    response = client.post(
        "/admin/models/import",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=payload
    )

    assert response.status_code == 201
    data = response.json()
    assert data["model_id"] == "azure/gpt-4o-existing"
    assert data["display_name"] == "Azure GPT-4o Existing"
    
    # Verify DB entry and association are created
    db_model = db.query(DBModel).filter(DBModel.model_id == "azure/gpt-4o-existing").first()
    assert db_model is not None
    assert db_model.display_name == "Azure GPT-4o Existing"

    # Verify model is associated with the region and set to synced
    association = db.query(DBModelRegion).filter(
        DBModelRegion.model_id == db_model.id,
        DBModelRegion.region_id == test_region.id
    ).first()
    assert association is not None
    assert association.is_active is True
    assert association.sync_status == "synced"






