"""Regression tests for the Stripe webhook endpoint (`POST /billing/events`).

These guard against the stripe-python v15 breaking change where ``StripeObject``
no longer subclasses ``dict``. Under v14.x, ``StripeObject`` inherited from
``Dict`` so ``event.get("id")`` worked; under v15+, ``event.get("id")`` raises
``AttributeError("get")``, which surfaced as an HTTP 500 on every webhook
delivery from Stripe (see Stripe's migration guide):
    https://github.com/stripe/stripe-python/wiki/Migration-guide-for-v15

IMPORTANT: these tests build REAL ``stripe.Event`` objects (via
``construct_from``), not ``SimpleNamespace`` mocks. A ``SimpleNamespace`` with
dict ``metadata`` would let ``.get()`` succeed and silently hide the regression.
A real ``Event`` under the pinned ``requirements.txt`` (``stripe==15.2.0``)
exhibits the same ``StripeObject`` internals as production and therefore
reproduces the bug. The handler must use attribute access (``getattr``).
"""

from unittest.mock import AsyncMock, patch

import pytest
import stripe
from stripe import Event

from app.db.models import DBStripeProcessedEvent
from app.services.stripe_webhook_classification import is_moad_webhook

WEBHOOK_SECRET_KEY = "stripe_webhook_secret"


def _make_real_stripe_event(
    event_id: str = "evt_test_regression_v15",
    event_type: str = "invoice.paid",
    customer_id: str = "cus_test_regression",
    metadata: dict | None = None,
) -> Event:
    """Build a REAL ``stripe.Event`` (not a SimpleNamespace).

    Under ``stripe >= 15`` the returned object's internals are ``StripeObject``
    instances that do NOT support ``.get()`` — exactly matching production.
    """
    return Event.construct_from(
        {
            "id": event_id,
            "type": event_type,
            "data": {
                "object": {
                    "customer": customer_id,
                    "metadata": metadata or {},
                }
            },
        },
        key="sk_test_regression",
    )


@pytest.fixture
def webhook_secret_env(monkeypatch):
    """Set ``WEBHOOK_SIG`` so the handler passes its secret guard.

    The secret value is irrelevant here because ``decode_stripe_event`` is
    patched in each test; we only need a non-empty value to avoid the 404
    "not configured" branch.
    """
    monkeypatch.setenv("WEBHOOK_SIG", "whsec_test_regression")
    yield


def test_webhook_endpoint_returns_200_for_real_stripe_event(
    client, db, webhook_secret_env
):
    """``POST /billing/events`` must return 200 for a real ``stripe.Event``.

    Regression for the v15 break: the handler previously called
    ``event.get("id")`` / ``event.get("type")``, which raises
    ``AttributeError("get")`` under ``stripe >= 15`` and made Stripe see a
    500 on every webhook delivery. The endpoint must read ``id``/``type`` via
    attribute access (``getattr``) instead.
    """
    real_event = _make_real_stripe_event()

    # decode_stripe_event already handles signature verification (tested
    # elsewhere); here we feed the handler a real Event to verify the
    # request path after verification can process a v15 StripeObject.
    with (
        patch("app.api.webhooks.decode_stripe_event", return_value=real_event),
        patch(
            "app.api.webhooks.handle_stripe_event_background", new_callable=AsyncMock
        ) as mock_background,
    ):
        response = client.post(
            "/billing/events",
            content=b'{"id": "evt_test_regression_v15"}',
            headers={
                "stripe-signature": "t=1,v1=fake",
                "content-type": "application/json",
            },
        )

    assert response.status_code == 200, (
        f"expected 200, got {response.status_code}: {response.text}"
    )
    # Background processing must be dispatched with the REAL event object.
    mock_background.assert_awaited_once_with(real_event)


def test_webhook_writes_idempotency_claim_from_real_stripe_event(
    client, db, webhook_secret_env
):
    """The idempotency claim row must be written using the real event id.

    This proves the handler extracts ``id``/``type`` from the real
    ``stripe.Event`` (via ``getattr``) rather than crashing before the insert.
    Under the buggy ``event.get("id")`` code this raised AttributeError -> 500
    and no row was ever written.
    """
    event_id = "evt_claim_from_real_event"
    real_event = _make_real_stripe_event(
        event_id=event_id, event_type="customer.subscription.created"
    )

    with (
        patch("app.api.webhooks.decode_stripe_event", return_value=real_event),
        patch(
            "app.api.webhooks.handle_stripe_event_background", new_callable=AsyncMock
        ),
    ):
        response = client.post(
            "/billing/events",
            content=b'{"id": "evt_claim_from_real_event"}',
            headers={
                "stripe-signature": "t=1,v1=fake",
                "content-type": "application/json",
            },
        )

    assert response.status_code == 200, response.text

    claim = (
        db.query(DBStripeProcessedEvent)
        .filter(DBStripeProcessedEvent.stripe_event_id == event_id)
        .first()
    )
    assert claim is not None, "idempotency claim row was not written"
    assert claim.event_type == "customer.subscription.created"


def test_webhook_duplicate_real_event_returns_200_not_500(
    client, db, webhook_secret_env
):
    """A duplicate real event hits the idempotency guard and returns 200.

    This exercises the ``IntegrityError`` branch — which the buggy code never
    reached (it 500'd before the insert). With the fix, the first delivery
    writes the claim row and a second delivery returns 200 cleanly.
    """
    event_id = "evt_duplicate_real_event"
    real_event = _make_real_stripe_event(event_id=event_id)

    with (
        patch("app.api.webhooks.decode_stripe_event", return_value=real_event),
        patch(
            "app.api.webhooks.handle_stripe_event_background", new_callable=AsyncMock
        ),
    ):
        first = client.post(
            "/billing/events",
            content=b'{"id": "evt_duplicate_real_event"}',
            headers={"stripe-signature": "t=1,v1=fake"},
        )
        second = client.post(
            "/billing/events",
            content=b'{"id": "evt_duplicate_real_event"}',
            headers={"stripe-signature": "t=1,v1=fake"},
        )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    # Exactly one claim row, despite two deliveries.
    claims = (
        db.query(DBStripeProcessedEvent)
        .filter(DBStripeProcessedEvent.stripe_event_id == event_id)
        .all()
    )
    assert len(claims) == 1


def test_webhook_skips_moad_checkout_session_events(client, db, webhook_secret_env):
    """MOAD checkout/session budget webhooks must be ACKed but not processed here."""
    event_id = "evt_moad_checkout_session"
    real_event = _make_real_stripe_event(
        event_id=event_id,
        event_type="checkout.session.completed",
        metadata={
            "teamId": "42",
            "regionId": "7",
            "ai_budget_increase": "5000",
        },
    )

    with (
        patch("app.api.webhooks.decode_stripe_event", return_value=real_event),
        patch(
            "app.api.webhooks.handle_stripe_event_background", new_callable=AsyncMock
        ) as mock_background,
    ):
        response = client.post(
            "/billing/events",
            content=b'{"id": "evt_moad_checkout_session"}',
            headers={"stripe-signature": "t=1,v1=fake"},
        )

    assert response.status_code == 200, response.text
    mock_background.assert_not_awaited()
    claim = (
        db.query(DBStripeProcessedEvent)
        .filter(DBStripeProcessedEvent.stripe_event_id == event_id)
        .first()
    )
    assert claim is None, "MOAD-owned events should not create legacy claims"


@patch("app.services.stripe_webhook_classification.stripe_sdk.Subscription.retrieve")
def test_webhook_skips_moad_invoice_events_via_subscription_metadata(
    mock_retrieve, client, db, webhook_secret_env
):
    """MOAD invoice webhooks may only expose markers on the subscription."""
    event_id = "evt_moad_invoice_paid"
    real_event = _make_real_stripe_event(event_id=event_id, event_type="invoice.paid")
    real_event.data.object.subscription = "sub_moad_123"
    mock_retrieve.return_value = stripe.Subscription.construct_from(
        {
            "id": "sub_moad_123",
            "metadata": {
                "ai_subscription": "true",
                "teamId": "42",
                "regionId": "7",
                "workspaceId": "ws_abc",
            },
        },
        key="sk_test_regression",
    )

    with (
        patch("app.api.webhooks.decode_stripe_event", return_value=real_event),
        patch(
            "app.api.webhooks.handle_stripe_event_background", new_callable=AsyncMock
        ) as mock_background,
    ):
        response = client.post(
            "/billing/events",
            content=b'{"id": "evt_moad_invoice_paid"}',
            headers={"stripe-signature": "t=1,v1=fake"},
        )

    assert response.status_code == 200, response.text
    mock_background.assert_not_awaited()
    claim = (
        db.query(DBStripeProcessedEvent)
        .filter(DBStripeProcessedEvent.stripe_event_id == event_id)
        .first()
    )
    assert claim is None, "MOAD-owned invoice events should not create legacy claims"


@patch("app.services.stripe_webhook_classification.stripe_sdk.Subscription.retrieve")
def test_is_moad_webhook_returns_false_for_legacy_invoice_without_markers(
    mock_retrieve,
):
    """Legacy invoice events must keep flowing through the legacy worker."""
    event = _make_real_stripe_event(
        event_id="evt_legacy_invoice", event_type="invoice.paid"
    )
    event.data.object.subscription = "sub_legacy_123"
    mock_retrieve.return_value = stripe.Subscription.construct_from(
        {"id": "sub_legacy_123", "metadata": {}},
        key="sk_test_regression",
    )

    assert is_moad_webhook(event) is False


def test_real_stripe_metadata_is_not_dict_like():
    """Guard test: documents the v15 contract that makes ``.get()`` unsafe.

    A real Stripe ``metadata`` object (``StripeObject``) must NOT be a ``dict``
    subclass under the pinned SDK. If this ever flips back (e.g. a downgrade or
    SDK revert), ``.get()`` would silently work again and the regression tests
    above would stop catching it — so this test makes the dependency explicit.

    Under ``stripe < 15`` this test is skipped (metadata IS dict-like there).
    """
    if int(stripe.VERSION.split(".")[0]) < 15:
        pytest.skip(
            f"stripe-python {stripe.VERSION} still subclasses dict; "
            "regression only applies to v15+"
        )

    event = _make_real_stripe_event(metadata={"regionId": "us103"})
    metadata = event.data.object.metadata

    # Under v15+ this must be a StripeObject, NOT a dict subclass.
    assert not isinstance(metadata, dict), (
        "stripe metadata is dict-like; the v15 .get() regression would NOT be "
        "caught by the endpoint tests above"
    )
    # Attribute access is the supported path.
    assert getattr(metadata, "regionId", None) == "us103"
