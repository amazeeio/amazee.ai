"""Tests for the model EOL scan (app/services/model_eol.py)."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.db.models import DBModel, DBRegion
from app.services import model_eol

HAIKU = "anthropic.claude-3-haiku-20240307-v1:0"
SONNET = "anthropic.claude-sonnet-4-20250514-v1:0"


def _make_region(db, name, active=True):
    region = DBRegion(
        name=name,
        postgres_host="host",
        postgres_port=5432,
        postgres_admin_user="user",
        postgres_admin_password="pass",
        litellm_api_url=f"https://{name}.example",
        litellm_api_key=f"{name}-key",
        is_active=active,
        is_dedicated=False,
    )
    db.add(region)
    db.commit()
    db.refresh(region)
    return region


def _make_model(db, model_id, **kwargs):
    kwargs.setdefault("provider", "bedrock")
    kwargs.setdefault("type", "chat")
    model = DBModel(model_id=model_id, display_name=model_id, **kwargs)
    db.add(model)
    db.commit()
    db.refresh(model)
    return model


def _litellm_data(*pairs):
    """LiteLLM /model/info payload from (model_name, backend_model) pairs."""
    return {
        "data": [
            {"model_name": name, "litellm_params": {"model": backend}}
            for name, backend in pairs
        ]
    }


def _catalog(*pairs):
    """Upstream catalog entries from (modelId, iso_eol_or_None) pairs."""
    entries = []
    for model_id, eol in pairs:
        entry = {"modelId": model_id, "modelLifecycle": {"status": "ACTIVE"}}
        if eol:
            entry["modelLifecycle"]["endOfLifeTime"] = f"{eol} 08:00:00+00:00"
        entries.append(entry)
    return entries


def _run_scan(db, catalog, litellm_payload, webhook_status=200):
    """Run the scan with a stubbed catalog, region and webhook. Returns (totals, posts)."""
    posts = []

    async def fake_post(url, json=None, headers=None):
        posts.append({"url": url, "json": json, "headers": headers})
        response = MagicMock()
        response.status_code = webhook_status
        response.text = ""
        return response

    client = MagicMock()
    client.post = AsyncMock(side_effect=fake_post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    service = MagicMock()
    service.get_model_info = AsyncMock(return_value=litellm_payload)

    with (
        patch.object(
            model_eol, "fetch_bedrock_catalog", AsyncMock(return_value=catalog)
        ),
        patch.object(model_eol, "LiteLLMService", return_value=service),
        patch.object(model_eol.httpx, "AsyncClient", return_value=client),
        patch.object(
            model_eol.settings, "BEDROCK_MODELS_URL", "https://catalog.test/models.json"
        ),
        patch.object(
            model_eol.settings,
            "MODEL_EOL_WEBHOOK_URL",
            "https://moad.test/webhook/amazeeai",
        ),
        patch.object(model_eol.settings, "MODEL_EOL_WEBHOOK_TOKEN", "tok"),
    ):
        totals = asyncio.run(model_eol.scan_models_for_eol(db))
    return totals, posts


# ---------------------------------------------------------------------------
# Upstream parsing
# ---------------------------------------------------------------------------


def test_build_eol_index_prefers_lifecycle_and_parses_card_dates():
    """Both upstream date fields are read; lifecycle wins when both are set."""
    index = model_eol.build_eol_index(
        [
            {
                "modelId": SONNET,
                "modelLifecycle": {"endOfLifeTime": "2026-10-14 07:00:00+00:00"},
                "modelCard": {"modelEolDate": "October 14, 2026"},
            },
            {
                "modelId": HAIKU,
                "modelLifecycle": {"status": "ACTIVE"},
                "modelCard": {"modelEolDate": "September 10, 2026"},
            },
            {
                "modelId": "anthropic.claude-opus-5",
                "modelLifecycle": {"status": "ACTIVE"},
                "modelCard": {"modelEolDate": None},
            },
            {"modelId": "broken.model", "modelCard": {"modelEolDate": "not a date"}},
        ]
    )
    assert index == {SONNET: "2026-10-14", HAIKU: "2026-09-10"}


@pytest.mark.parametrize(
    "backend,expected",
    [
        (f"bedrock/us.{HAIKU}", HAIKU),
        (f"bedrock/eu.{HAIKU}", HAIKU),
        (f"bedrock/{HAIKU}", HAIKU),
        ("vertex_ai/gemini-2.5-pro", None),
        ("azure/gpt-4.1", None),
        ("", None),
    ],
)
def test_bedrock_catalog_id_strips_geo_prefixes(backend, expected):
    assert (
        model_eol.bedrock_catalog_id({"litellm_params": {"model": backend}}) == expected
    )


@pytest.mark.asyncio
async def test_catalog_is_fetched_once_under_concurrency():
    """Concurrent callers share one HTTP fetch, so a fan-out costs one GET."""
    model_eol._bedrock_catalog_cache["url"] = None
    model_eol._bedrock_catalog_cache["data"] = None
    model_eol._bedrock_catalog_cache["expires_at"] = datetime.min.replace(tzinfo=UTC)

    catalog = _catalog((HAIKU, "2026-09-10"))
    response = MagicMock()
    response.json.return_value = catalog
    response.raise_for_status.return_value = None
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch.object(model_eol.httpx, "AsyncClient", return_value=client):
        results = await asyncio.gather(
            *(
                model_eol.fetch_bedrock_catalog("https://catalog.test/models.json")
                for _ in range(5)
            )
        )

    assert client.get.await_count == 1
    assert all(result == catalog for result in results)

    model_eol._bedrock_catalog_cache["url"] = None
    model_eol._bedrock_catalog_cache["data"] = None
    model_eol._bedrock_catalog_cache["expires_at"] = datetime.min.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Stored dates
# ---------------------------------------------------------------------------


def test_eol_dates_by_model_returns_only_live_dated_models(db):
    _make_model(db, "with-date", upstream_eol=datetime(2026, 9, 10, tzinfo=UTC))
    _make_model(db, "no-date")
    _make_model(
        db,
        "deleted",
        upstream_eol=datetime(2026, 9, 10, tzinfo=UTC),
        deleted_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert model_eol.eol_dates_by_model(db) == {"with-date": "2026-09-10"}


# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------


def test_scan_stores_date_and_sends_one_snapshot(db):
    _make_region(db, "us1")
    model = _make_model(db, "claude-3-haiku")
    _make_model(db, "gemini-2.5-pro", provider="vertex_ai")

    totals, posts = _run_scan(
        db,
        _catalog((HAIKU, "2026-09-10"), (SONNET, None)),
        _litellm_data(
            ("claude-3-haiku", f"bedrock/us.{HAIKU}"),
            ("gemini-2.5-pro", "vertex_ai/gemini-2.5-pro"),
        ),
    )

    db.refresh(model)
    assert model.upstream_eol == datetime(2026, 9, 10, tzinfo=UTC)
    assert model.upstream_eol_first_seen_at is not None
    assert model.eol_notified_at is not None

    assert len(posts) == 1, "one request per day, not one per model"
    body = posts[0]["json"]
    assert body["events"] == [
        {
            "event_id": "model_eol:claude-3-haiku:2026-09-10",
            "type": "model.eol_announced",
            "created_at": body["events"][0]["created_at"],
            "data": {
                "model_id": "claude-3-haiku",
                "display_name": "claude-3-haiku",
                "eol_date": "2026-09-10",
                "first_seen_at": model.upstream_eol_first_seen_at.isoformat(),
                "regions": ["us1"],
            },
        }
    ]
    assert posts[0]["headers"]["Authorization"] == "Bearer tok"
    assert totals["dates_set"] == 1
    assert totals["newly_notified"] == 1


def test_scan_resends_the_same_snapshot_without_restamping(db):
    """A repeat day is a full snapshot again, but announces nothing new."""
    _make_region(db, "us1")
    model = _make_model(db, "claude-3-haiku")
    catalog = _catalog((HAIKU, "2026-09-10"))
    payload = _litellm_data(("claude-3-haiku", f"bedrock/us.{HAIKU}"))

    _run_scan(db, catalog, payload)
    db.refresh(model)
    first_seen, notified = model.upstream_eol_first_seen_at, model.eol_notified_at

    totals, posts = _run_scan(db, catalog, payload)

    db.refresh(model)
    assert model.upstream_eol_first_seen_at == first_seen
    assert model.eol_notified_at == notified
    assert len(posts[0]["json"]["events"]) == 1
    assert totals["dates_set"] == 0
    assert totals["newly_notified"] == 0


def test_scan_reannounces_a_changed_date(db):
    _make_region(db, "us1")
    model = _make_model(db, "claude-3-haiku")
    payload = _litellm_data(("claude-3-haiku", f"bedrock/us.{HAIKU}"))

    _run_scan(db, _catalog((HAIKU, "2026-09-10")), payload)
    db.refresh(model)
    first_seen = model.upstream_eol_first_seen_at

    _, posts = _run_scan(db, _catalog((HAIKU, "2026-12-01")), payload)

    db.refresh(model)
    assert model.upstream_eol == datetime(2026, 12, 1, tzinfo=UTC)
    assert model.upstream_eol_first_seen_at > first_seen
    assert model.eol_notified_at is not None
    assert (
        posts[0]["json"]["events"][0]["event_id"]
        == "model_eol:claude-3-haiku:2026-12-01"
    )


def test_scan_clears_a_withdrawn_date(db):
    _make_region(db, "us1")
    model = _make_model(db, "claude-3-haiku")
    payload = _litellm_data(("claude-3-haiku", f"bedrock/us.{HAIKU}"))

    _run_scan(db, _catalog((HAIKU, "2026-09-10")), payload)
    totals, posts = _run_scan(db, _catalog((HAIKU, None)), payload)

    db.refresh(model)
    assert model.upstream_eol is None
    assert model.upstream_eol_first_seen_at is None
    assert totals["dates_cleared"] == 1
    assert posts == [], "nothing to announce, so no request at all"


def test_unreadable_region_does_not_clear_stored_dates(db):
    """A region outage must not look like every model losing its EOL date."""
    _make_region(db, "us1")
    model = _make_model(
        db,
        "claude-3-haiku",
        upstream_eol=datetime(2026, 9, 10, tzinfo=UTC),
        upstream_eol_first_seen_at=datetime(2026, 8, 1, tzinfo=UTC),
        eol_notified_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    service = MagicMock()
    service.get_model_info = AsyncMock(side_effect=httpx.ConnectError("boom"))
    client = MagicMock()
    client.post = AsyncMock(return_value=MagicMock(status_code=200, text=""))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch.object(
            model_eol,
            "fetch_bedrock_catalog",
            AsyncMock(return_value=_catalog((HAIKU, "2026-09-10"))),
        ),
        patch.object(model_eol, "LiteLLMService", return_value=service),
        patch.object(model_eol.httpx, "AsyncClient", return_value=client),
        patch.object(
            model_eol.settings, "BEDROCK_MODELS_URL", "https://catalog.test/models.json"
        ),
        patch.object(
            model_eol.settings, "MODEL_EOL_WEBHOOK_URL", "https://moad.test/hook"
        ),
    ):
        totals = asyncio.run(model_eol.scan_models_for_eol(db))

    db.refresh(model)
    assert model.upstream_eol == datetime(2026, 9, 10, tzinfo=UTC)
    assert totals["regions_unreadable"] == 1
    assert totals["dates_cleared"] == 0


def test_scan_aborts_without_touching_the_db_when_the_catalog_is_down(db):
    _make_region(db, "us1")
    model = _make_model(
        db, "claude-3-haiku", upstream_eol=datetime(2026, 9, 10, tzinfo=UTC)
    )

    with (
        patch.object(
            model_eol,
            "fetch_bedrock_catalog",
            AsyncMock(side_effect=httpx.ConnectError("boom")),
        ),
        patch.object(
            model_eol.settings, "BEDROCK_MODELS_URL", "https://catalog.test/models.json"
        ),
    ):
        totals = asyncio.run(model_eol.scan_models_for_eol(db))

    db.refresh(model)
    assert model.upstream_eol == datetime(2026, 9, 10, tzinfo=UTC)
    assert totals == {}


def test_failed_delivery_leaves_the_model_unnotified(db):
    """A rejected snapshot must be re-announced on the next run."""
    _make_region(db, "us1")
    model = _make_model(db, "claude-3-haiku")

    totals, posts = _run_scan(
        db,
        _catalog((HAIKU, "2026-09-10")),
        _litellm_data(("claude-3-haiku", f"bedrock/us.{HAIKU}")),
        webhook_status=500,
    )

    db.refresh(model)
    assert model.upstream_eol == datetime(2026, 9, 10, tzinfo=UTC)
    assert model.eol_notified_at is None
    assert len(posts) == 1
    assert totals["newly_notified"] == 0


def test_scan_skips_served_models_without_a_models_row(db):
    """A model served but absent from the catalog table is skipped, not created."""
    _make_region(db, "us1")

    totals, posts = _run_scan(
        db,
        _catalog((HAIKU, "2026-09-10")),
        _litellm_data(("claude-3-haiku", f"bedrock/us.{HAIKU}")),
    )

    assert db.query(DBModel).count() == 0
    assert totals["dates_set"] == 0
    assert posts == []


def test_alias_does_not_inherit_its_target_eol(db):
    """A fake-alias LiteLLM entry carries the target's backend model id.

    Without the is_alias filter the alias row would pick up the target's date
    and be announced as retiring, which it never is -- it gets repointed.
    """
    _make_region(db, "us1")
    target = _make_model(db, "claude-3-haiku")
    alias = _make_model(db, "chat", is_alias=True)

    _, posts = _run_scan(
        db,
        _catalog((HAIKU, "2026-09-10")),
        _litellm_data(
            ("claude-3-haiku", f"bedrock/us.{HAIKU}"),
            ("chat", f"bedrock/us.{HAIKU}"),
        ),
    )

    db.refresh(target)
    db.refresh(alias)
    assert target.upstream_eol == datetime(2026, 9, 10, tzinfo=UTC)
    assert alias.upstream_eol is None
    assert [e["data"]["model_id"] for e in posts[0]["json"]["events"]] == [
        "claude-3-haiku"
    ]


def test_deactivated_model_is_left_out_of_the_snapshot(db):
    """A globally deactivated model is off the callable surface already."""
    _make_region(db, "us1")
    _make_model(
        db,
        "claude-3-haiku",
        upstream_eol=datetime(2026, 9, 10, tzinfo=UTC),
        is_active_globally=False,
    )

    _, posts = _run_scan(db, _catalog((HAIKU, "2026-09-10")), {"data": []})

    assert posts == []


def test_unset_webhook_url_leaves_the_model_unnotified(db):
    """Without a destination the dates are still stored, but nothing is sent."""
    _make_region(db, "us1")
    model = _make_model(db, "claude-3-haiku")

    service = MagicMock()
    service.get_model_info = AsyncMock(
        return_value=_litellm_data(("claude-3-haiku", f"bedrock/us.{HAIKU}"))
    )

    with (
        patch.object(
            model_eol,
            "fetch_bedrock_catalog",
            AsyncMock(return_value=_catalog((HAIKU, "2026-09-10"))),
        ),
        patch.object(model_eol, "LiteLLMService", return_value=service),
        patch.object(
            model_eol.settings, "BEDROCK_MODELS_URL", "https://catalog.test/models.json"
        ),
        patch.object(model_eol.settings, "MODEL_EOL_WEBHOOK_URL", ""),
    ):
        totals = asyncio.run(model_eol.scan_models_for_eol(db))

    db.refresh(model)
    assert model.upstream_eol == datetime(2026, 9, 10, tzinfo=UTC)
    assert model.eol_notified_at is None
    assert totals["newly_notified"] == 0
