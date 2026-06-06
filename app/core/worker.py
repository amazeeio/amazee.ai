import os
from datetime import UTC, datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import select, func, and_, or_, update as sa_update
from app.db.models import (
    DBTeam,
    DBAuditLog,
    DBProduct,
    DBTeamProduct,
    DBPrivateAIKey,
    DBUser,
    DBRegion,
    DBTeamMetrics,
    DBLimitedResource,
    DBTeamRegion,
    DBPoolPurchase,
    DBPeriodicPayment,
    DBPeriodicBudgetLedgerEntry,
    DBAPIToken,
    DBUserAdminRegion,
    DBSpendCap,
    DBUserSpendCache,
)
from app.schemas.models import BudgetType
from app.services.litellm import LiteLLMService
from app.services.ses import SESService
from app.core.team_service import (
    get_team_keys_by_region,
    get_team_region_litellm_keys,
    soft_delete_team,
)
from app.db.database import get_db
from app.core.limit_service import LimitService
from app.schemas.limits import ResourceType, UnitType, OwnerType, LimitedResource
import logging
from collections import defaultdict

# get_token_restrictions is now available through LimitService
from app.services.stripe import (
    get_subscribed_products_for_customer,
    get_product_id_from_subscription,
    get_product_id_from_session,
    stripe_sdk,
    KNOWN_EVENTS,
    SUBSCRIPTION_SUCCESS_EVENTS,
    INVOICE_SUCCESS_EVENTS,
    SESSION_SUCCESS_EVENTS,
    SESSION_FAILURE_EVENTS,
    SUBSCRIPTION_FAILURE_EVENTS,
    INVOICE_FAILURE_EVENTS,
)
from prometheus_client import Gauge, Counter, Summary
from typing import Dict, List, Optional
from app.core.security import create_access_token
from app.core.config import settings
from urllib.parse import urljoin
from app.core.spend_period_service import (
    fetch_team_spend_snapshot_for_region,
    upsert_team_spend_period,
)
from app.core.periodic_budget_ledger_service import (
    BudgetDriftResult,
    add_subscription_entry,
    allocate_period_spend_fifo,
    compute_active_topup_remaining,
    expire_subscription_entries,
    materialize_topup_rollovers,
)
from app.core.email import normalize_email_for_lookup

logger = logging.getLogger(__name__)

FIRST_EMAIL_DAYS_LEFT = 7
SECOND_EMAIL_DAYS_LEFT = 5
TRIAL_OVER_DAYS = 30

# Budget types that support subscription cycles (PERIODIC and POOL).
# Used to gate cycle/ledger/drift functions that were originally PERIODIC-only.
SUBSCRIPTION_BUDGET_TYPES = frozenset({BudgetType.PERIODIC, BudgetType.POOL})

# Prometheus metrics
team_freshness_days = Gauge(
    "team_freshness_days",
    "Freshness of teams in days (since creation for teams without products, since last payment for teams with products)",
    ["team_id", "team_name"],
)

team_expired_metric = Counter(
    "team_expired_total",
    "Total number of teams that have expired without products",
    ["team_id", "team_name"],
)

team_monitoring_failed_metric = Counter(
    "team_monitoring_failed_total",
    "Total number of teams that failed to be monitored due to errors",
    ["team_id", "team_name", "error_type"],
)

key_spend_percentage = Gauge(
    "key_spend_percentage",
    "Percentage of budget used for each key",
    ["team_id", "team_name", "key_alias"],
)

# Retention metrics
team_retention_warning_sent_total = Counter(
    "team_retention_warning_sent_total",
    "Total number of retention warnings sent to teams",
    ["team_id", "team_name"],
)

team_retention_deleted_total = Counter(
    "team_retention_deleted_total",
    "Total number of teams deleted due to retention policy",
    ["team_id", "team_name"],
)

team_days_since_activity = Gauge(
    "team_days_since_activity",
    "Days since last activity for each team",
    ["team_id", "team_name"],
)

team_total_spend = Gauge(
    "team_total_spend",
    "Total spend across all keys in a team for the current budget period",
    ["team_id", "team_name"],
)

team_hard_deleted_total = Counter(
    "team_hard_deleted_total",
    "Total number of teams hard deleted after retention period",
    ["team_id", "team_name"],
)

monitor_teams_duration = Summary(
    "monitor_teams_duration_seconds", "Time taken to complete the monitor_teams task"
)

hard_delete_teams_duration = Summary(
    "hard_delete_teams_duration_seconds",
    "Time spent executing the hard delete teams job",
)

# Track active team labels to zero out metrics for inactive teams
active_team_labels = set()


def _parse_client_reference_ids(
    client_reference_id: str | None,
) -> tuple[int, int] | None:
    """Parse Stripe pricing table client_reference_id in '<team_id>-<region_id>' form."""
    if not client_reference_id:
        return None
    parts = client_reference_id.split("-", 1)
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return None


async def _backfill_subscription_metadata_from_checkout_session(
    db: Session, event_object
) -> None:
    """Backfill Stripe subscription metadata using checkout session client_reference_id."""
    subscription_id = getattr(event_object, "subscription", None)
    if not subscription_id:
        return

    parsed = _parse_client_reference_ids(
        getattr(event_object, "client_reference_id", None)
    )
    if not parsed:
        return
    team_id, region_id = parsed

    customer_id = getattr(event_object, "customer", None)
    if customer_id:
        team = db.query(DBTeam).filter(DBTeam.stripe_customer_id == customer_id).first()
        if team and team.id != team_id:
            logger.warning(
                "Skipping subscription metadata backfill: client_reference_id team=%s does not match customer team=%s",
                team_id,
                team.id,
            )
            return

    try:
        stripe_sdk.Subscription.modify(
            subscription_id,
            metadata={"teamId": str(team_id), "regionId": str(region_id)},
        )
        logger.info(
            "Backfilled subscription metadata for sub=%s teamId=%s regionId=%s",
            subscription_id,
            team_id,
            region_id,
        )
    except Exception as exc:
        logger.warning(
            "Failed to backfill subscription metadata for sub=%s: %s",
            subscription_id,
            exc,
        )


def set_team_and_user_limits(db: Session, team: DBTeam):
    """
    Set limits for a team and all users in the team.

    This function:
    1. Sets team limits using the limit service
    2. Sets user limits for all users in the team
    3. Updates current values for COUNT-type limits based on actual database counts

    Args:
        db: Database session
        team: The team to set limits for
    """
    # Ensure all limits are correct - will not override MANUAL limits
    limit_service = LimitService(db)
    limit_service.set_team_limits(team)

    # Set user limits for all users in the team
    team_users = db.query(DBUser).filter(DBUser.team_id == team.id).all()
    for user in team_users:
        limit_service.set_user_limits(user)

    # Update current values for COUNT-type team limits
    team_limits = limit_service.get_team_limits(team)
    for limit in team_limits:
        # Set the value of the limit if not already set
        if limit.unit == UnitType.COUNT and limit.current_value == 0.0:
            if limit.resource == ResourceType.USER:
                value = db.execute(
                    select(func.count())
                    .select_from(DBUser)
                    .where(DBUser.team_id == team.id)
                ).scalar()
            elif limit.resource == ResourceType.SERVICE_KEY:
                value = db.execute(
                    select(func.count())
                    .select_from(DBPrivateAIKey)
                    .where(
                        DBPrivateAIKey.team_id == team.id,
                        DBPrivateAIKey.owner_id.is_(None),  # Service keys have no owner
                        DBPrivateAIKey.litellm_token.is_not(None),
                    )
                ).scalar()
            elif limit.resource == ResourceType.VECTOR_DB:
                value = db.execute(
                    select(func.count())
                    .select_from(DBPrivateAIKey)
                    .where(
                        DBPrivateAIKey.team_id == team.id,
                        DBPrivateAIKey.owner_id.is_(
                            None
                        ),  # Only count team-owned vector DBs
                        DBPrivateAIKey.database_username.is_not(None),
                    )
                ).scalar()
            else:
                # Skip unsupported resource types - they don't need current_value updates
                continue
            limit_service.set_current_value(limit, value)

    # Update current values for COUNT-type user limits
    for user in team_users:
        user_limits = limit_service.get_user_limits(user)
        for limit in user_limits:
            # Set the value of the limit if not already set
            if limit.unit == UnitType.COUNT and limit.current_value == 0.0:
                if limit.resource == ResourceType.USER_KEY:
                    # Count keys owned by this specific user
                    value = db.execute(
                        select(func.count())
                        .select_from(DBPrivateAIKey)
                        .where(
                            DBPrivateAIKey.owner_id == user.id,
                            DBPrivateAIKey.litellm_token.is_not(None),
                        )
                    ).scalar()
                    limit_service.set_current_value(limit, value)
                else:
                    # Skip unsupported resource types - they don't need current_value updates
                    continue


async def _record_periodic_payment(db: Session, event_object: any) -> Optional[int]:
    """
    Record a periodic team payment (subscription or top-up) in the database.
    Used for audit and to track LiteLLM synchronization status.
    """
    try:
        customer_id = getattr(event_object, "customer", None)
        if not customer_id:
            return None

        team = db.query(DBTeam).filter(DBTeam.stripe_customer_id == customer_id).first()
        if not team:
            logger.warning(f"No team found for Stripe customer ID: {customer_id}")
            return None

        stripe_payment_id = event_object.id
        # Extract amount and currency based on object type (Invoice or Session)
        raw_amount = getattr(
            event_object, "amount_paid", getattr(event_object, "amount_total", 0)
        )
        try:
            amount_cents = int(raw_amount)
        except (TypeError, ValueError):
            amount_cents = 0

        raw_currency = getattr(event_object, "currency", "usd")
        currency = str(raw_currency).lower() if isinstance(raw_currency, str) else "usd"

        # Determine payment type from metadata
        metadata = getattr(event_object, "metadata", {})
        payment_type = "subscription"
        if metadata and metadata.get("ai_budget_increase"):
            payment_type = "topup"

        # Check if record already exists to avoid duplicates
        payment_record = (
            db.query(DBPeriodicPayment)
            .filter(DBPeriodicPayment.stripe_payment_id == stripe_payment_id)
            .first()
        )

        if not payment_record:
            payment_record = DBPeriodicPayment(
                team_id=team.id,
                stripe_payment_id=stripe_payment_id,
                amount_cents=amount_cents,
                currency=currency,
                payment_type=payment_type,
                status="completed",
                sync_status="pending",
                payment_date=datetime.now(UTC),
            )
            db.add(payment_record)
            db.commit()
            logger.info(
                f"Recorded {payment_type} payment {stripe_payment_id} for team {team.id}"
            )

        return payment_record.id
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to record periodic payment: {str(e)}")
        return None


async def _record_periodic_payment_direct(
    db: Session,
    *,
    team_id: int,
    transaction_id: str,
    amount_cents: int,
    currency: str = "usd",
    payment_type: str = "subscription",
) -> Optional[int]:
    """Record a periodic team payment using direct billing payload fields."""
    try:
        team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
        if not team:
            logger.warning(f"No team found for team ID: {team_id}")
            return None

        payment_record = (
            db.query(DBPeriodicPayment)
            .filter(DBPeriodicPayment.stripe_payment_id == transaction_id)
            .first()
        )

        if not payment_record:
            payment_record = DBPeriodicPayment(
                team_id=team.id,
                stripe_payment_id=transaction_id,
                amount_cents=amount_cents,
                currency=currency.lower(),
                payment_type=payment_type,
                status="completed",
                sync_status="success",
                payment_date=datetime.now(UTC),
            )
            db.add(payment_record)
            db.commit()
            logger.info(
                "Recorded %s payment %s for team %s",
                payment_type,
                transaction_id,
                team.id,
            )

        return payment_record.id
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to record periodic payment: {str(e)}")
        return None


async def _run_cycle_from_stripe_event(
    *,
    db: Session,
    event_id: str | None,
    customer_id: str,
    event_object,
) -> None:
    """Run the /cycle pipeline for an invoice.paid Stripe event.

    Extracts team_id, region_id, and budget_cents from the Stripe payload
    using the same resolution logic as the /cycle endpoint, then calls
    the same worker functions.
    """
    # --- Resolve team ---
    team = db.query(DBTeam).filter(DBTeam.stripe_customer_id == customer_id).first()
    if not team:
        logger.warning("No team found for customer_id=%s", customer_id)
        return
    if team.budget_type not in SUBSCRIPTION_BUDGET_TYPES:
        logger.info(
            "Skipping invoice.paid: team %s budget_type=%s does not support subscription cycles",
            team.id,
            team.budget_type,
        )
        return

    # --- Resolve region ---
    # Try subscription metadata from invoice parent first (no API call needed),
    # then try Stripe API, then fall back to DBTeamRegion
    region_id: int | None = None
    subscription_id = getattr(event_object, "subscription", None)
    sub_meta: dict = {}

    # Check parent.subscription_details on the invoice object
    if hasattr(event_object, "parent"):
        try:
            details = event_object.parent.subscription_details
            subscription_id = getattr(details, "subscription", subscription_id)
            sub_meta = getattr(details, "metadata", {}) or {}
        except AttributeError:
            logger.debug(
                "Invoice parent.subscription_details not available; continuing with fallback region resolution"
            )

    if sub_meta.get("regionId"):
        try:
            region_id = int(sub_meta["regionId"])
        except (TypeError, ValueError) as exc:
            logger.warning(
                "Invalid regionId in subscription metadata for customer_id=%s: %r (%s)",
                customer_id,
                sub_meta.get("regionId"),
                exc,
            )

    # Fallback: fetch subscription from Stripe API
    if not region_id and subscription_id:
        try:
            sub = stripe_sdk.Subscription.retrieve(subscription_id)
            meta = getattr(sub, "metadata", {}) or {}
            if meta.get("regionId"):
                region_id = int(meta["regionId"])
        except Exception as exc:
            logger.warning(
                "Failed to retrieve subscription %s: %s", subscription_id, exc
            )

    target_regions: list[DBRegion] = []
    if region_id:
        region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
        if not region:
            logger.error("Region %s not found for team %s", region_id, team.id)
            return
        target_regions = [region]
    else:
        # Runtime safety fallback for legacy subscriptions without metadata:
        # apply the same subscription cycle budget across all team regions,
        # matching pre-PR webhook behavior.
        team_regions = (
            db.query(DBTeamRegion).filter(DBTeamRegion.team_id == team.id).all()
        )
        if not team_regions:
            logger.error("Cannot resolve any region for team %s", team.id)
            return
        region_ids = [tr.region_id for tr in team_regions]
        target_regions = db.query(DBRegion).filter(DBRegion.id.in_(region_ids)).all()
        if not target_regions:
            logger.error("No valid regions found for team %s", team.id)
            return
        logger.warning(
            "Missing regionId metadata for team=%s customer=%s subscription=%s; "
            "falling back to all team regions (%s)",
            team.id,
            customer_id,
            subscription_id,
            len(target_regions),
        )

    # --- Resolve budget ---
    amount_paid = int(getattr(event_object, "amount_paid", 0) or 0)
    budget_cents = amount_paid

    if budget_cents == 0 and subscription_id:
        # Free plan — look up product budget from DB
        try:
            product_id = await get_product_id_from_subscription(subscription_id)
            product = db.query(DBProduct).filter(DBProduct.id == product_id).first()
            if product and product.max_budget_per_key:
                budget_cents = int(round(product.max_budget_per_key * 100))
        except Exception as exc:
            logger.warning(
                "Failed to resolve free-plan product budget for subscription %s: %s",
                subscription_id,
                exc,
            )

    # --- Transaction ID for idempotency ---
    transaction_id = getattr(event_object, "id", None) or event_id

    # --- Idempotency check (same as /cycle) ---
    existing = (
        db.query(DBPeriodicPayment)
        .filter(DBPeriodicPayment.stripe_payment_id == transaction_id)
        .first()
    )
    if existing and existing.sync_status == "success":
        logger.info(
            "Webhook invoice.paid idempotent skip: transaction_id=%s",
            transaction_id,
        )
        return

    # --- Run the same /cycle pipeline ---
    period_start = datetime.now(UTC)
    # Safety-net: Stripe cycles are 30d. The 31d budget_duration on LiteLLM
    # auto-expires budget if a webhook is missed. On cancellation, Stripe sends
    # customer.subscription.deleted which handles explicit cleanup.
    period_end = period_start + timedelta(days=31)

    target_region_ids = [region.id for region in target_regions]
    is_first_cycle = (
        not db.query(DBPeriodicBudgetLedgerEntry)
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == team.id,
            DBPeriodicBudgetLedgerEntry.region_id.in_(target_region_ids),
            DBPeriodicBudgetLedgerEntry.entry_type == "subscription",
        )
        .first()
    )

    try:
        if not is_first_cycle:
            for region in target_regions:
                await capture_periodic_team_spend_for_period(
                    db=db,
                    team=team,
                    region=region,
                    period_start=period_start,
                    period_end=period_end,
                    source_event_id=event_id,
                )

        payment_id = await _record_periodic_payment_direct(
            db,
            team_id=team.id,
            transaction_id=transaction_id,
            amount_cents=budget_cents,
            currency=getattr(event_object, "currency", "usd") or "usd",
            payment_type="subscription",
        )

        sync_errors: list[str] = []
        for region in target_regions:
            await _sync_periodic_ledger_for_period(
                db=db,
                team=team,
                region=region,
                period_start=period_start,
                period_end=period_end,
                amount_cents=budget_cents,
                source_payment_id=payment_id,
                source_invoice_id=transaction_id,
            )

            region_errors = await apply_billing_cycle_for_team(
                db=db,
                team_id=team.id,
                budget_cents=budget_cents,
                region_id=region.id,
                period_start=period_start,
                period_end=period_end,
                source_payment_id=payment_id,
            )
            sync_errors.extend(region_errors)

        logger.info(
            "Webhook invoice.paid cycle complete: team=%s invoice=%s budget=%s errors=%s",
            team.id,
            transaction_id,
            budget_cents,
            len(sync_errors),
        )
    except Exception as exc:
        logger.error(
            "Webhook invoice.paid cycle failed: team=%s invoice=%s error=%s",
            team.id,
            transaction_id,
            exc,
            exc_info=True,
        )


async def handle_stripe_event_background(event):
    """Background task to handle Stripe webhook events.

    Creates its own database session to avoid using the request-scoped session.
    """
    db = next(get_db())
    try:
        event_type = event.type
        if event_type not in KNOWN_EVENTS:
            logger.info("Unknown event type: %s", event_type)
            return

        event_object = event.data.object
        event_id = getattr(event, "id", None)
        customer_id = event_object.customer
        if not customer_id:
            logger.warning("No customer ID found in event, cannot complete processing")
            return

        # --- Success events ---
        if event_type in INVOICE_SUCCESS_EVENTS:
            # Use the /cycle pipeline for invoice.paid — same as MOAD subscription.cycle
            await _run_cycle_from_stripe_event(
                db=db,
                event_id=event_id,
                customer_id=customer_id,
                event_object=event_object,
            )

        elif event_type in SUBSCRIPTION_SUCCESS_EVENTS:
            product_id = await get_product_id_from_subscription(event_object.id)
            start_date = datetime.fromtimestamp(event_object.start_date, tz=UTC)
            await apply_product_for_team(db, customer_id, product_id, start_date)

        elif event_type in SESSION_SUCCESS_EVENTS:
            await _backfill_subscription_metadata_from_checkout_session(
                db, event_object
            )
            subscription = getattr(event_object, "subscription", None)
            if subscription:
                product_id = await get_product_id_from_subscription(subscription)
                await apply_product_for_team(
                    db, customer_id, product_id, datetime.now(UTC)
                )
            else:
                metadata = getattr(event_object, "metadata", {})
                if metadata and metadata.get("ai_budget_increase"):
                    team = (
                        db.query(DBTeam)
                        .filter(DBTeam.stripe_customer_id == customer_id)
                        .first()
                    )
                    if team and team.products:
                        product_id = team.products[0].id
                        await apply_product_for_team(
                            db,
                            customer_id,
                            product_id,
                            datetime.now(UTC),
                        )

        # --- Failure events ---
        elif event_type in SESSION_FAILURE_EVENTS:
            product_id = await get_product_id_from_session(event_object.id)
            await remove_product_from_team(db, customer_id, product_id)

        elif event_type in SUBSCRIPTION_FAILURE_EVENTS:
            product_id = await get_product_id_from_subscription(event_object.id)
            await remove_product_from_team(db, customer_id, product_id)

        elif event_type in INVOICE_FAILURE_EVENTS:
            subscription = getattr(event_object, "subscription", None)
            if not subscription and hasattr(event_object, "parent"):
                try:
                    subscription = event_object.parent.subscription_details.subscription
                except AttributeError:
                    logger.debug(
                        "Invoice event missing parent.subscription_details.subscription"
                    )

            if subscription:
                product_id = await get_product_id_from_subscription(subscription)
                await remove_product_from_team(db, customer_id, product_id)

    except Exception as exc:
        logger.error("Error in background event handler: %s", exc, exc_info=True)
    finally:
        db.close()


async def remove_product_from_team(db: Session, customer_id: str, product_id: str):
    """Remove a product association from a team after verifying Stripe subscription is gone."""
    try:
        team = db.query(DBTeam).filter(DBTeam.stripe_customer_id == customer_id).first()
        product = db.query(DBProduct).filter(DBProduct.id == product_id).first()

        if not team or not product:
            logger.error(
                "Team or product not found: customer=%s product=%s",
                customer_id,
                product_id,
            )
            return

        existing = (
            db.query(DBTeamProduct)
            .filter(
                DBTeamProduct.team_id == team.id, DBTeamProduct.product_id == product.id
            )
            .first()
        )
        if not existing:
            return

        # Verify subscription is no longer active in Stripe
        try:
            stripe_subs = await get_subscribed_products_for_customer(customer_id)
            for _, stripe_product_id in stripe_subs:
                if stripe_product_id == product_id:
                    logger.warning(
                        "Product %s still active in Stripe for customer %s. Not removing.",
                        product_id,
                        customer_id,
                    )
                    return
        except Exception as exc:
            logger.error("Cannot verify Stripe status for %s: %s", customer_id, exc)
            return

        db.delete(existing)
        limit_service = LimitService(db)
        limit_service.set_team_limits(team)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Error removing product from team: %s", exc)


async def capture_periodic_team_spend_for_invoice(
    *,
    db: Session,
    customer_id: str,
    invoice_obj,
    stripe_event_id: str | None,
    region_id: int | None,
) -> None:
    team = db.query(DBTeam).filter(DBTeam.stripe_customer_id == customer_id).first()
    if team is None:
        logger.warning(
            "Skipping spend period capture: no team for customer_id=%s", customer_id
        )
        return
    if team.budget_type not in SUBSCRIPTION_BUDGET_TYPES:
        return

    period_start_ts = getattr(invoice_obj, "period_start", None)
    period_end_ts = getattr(invoice_obj, "period_end", None)
    if period_start_ts is None or period_end_ts is None:
        logger.warning(
            "Skipping spend period capture: missing period_start/period_end for team_id=%s",
            team.id,
        )
        return

    period_start = datetime.fromtimestamp(period_start_ts, tz=UTC)
    period_end = datetime.fromtimestamp(period_end_ts, tz=UTC)

    if region_id is None:
        logger.warning(
            "Skipping spend period capture: missing region_id for periodic team_id=%s",
            team.id,
        )
        return
    region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not region:
        logger.warning(
            "Skipping spend period capture: region_id=%s not found for periodic team_id=%s",
            region_id,
            team.id,
        )
        return

    try:
        snapshot = await fetch_team_spend_snapshot_for_region(
            db=db,
            team=team,
            region=region,
        )
        upsert_team_spend_period(
            db=db,
            team=team,
            region_id=region.id,
            period_start=period_start,
            period_end=period_end,
            source="stripe_webhook_litellm_sync",
            snapshot=snapshot,
            stripe_event_id=stripe_event_id,
            stripe_invoice_id=getattr(invoice_obj, "id", None),
            stripe_subscription_id=getattr(
                getattr(
                    getattr(invoice_obj, "parent", None),
                    "subscription_details",
                    None,
                ),
                "subscription",
                None,
            ),
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            "Failed to capture spend period for team_id=%s region_id=%s: %s",
            team.id,
            region.id,
            str(exc),
        )


async def capture_periodic_team_spend_for_period(
    *,
    db: Session,
    team: DBTeam,
    region: DBRegion,
    period_start: datetime,
    period_end: datetime,
    source_event_id: str | None,
) -> None:
    if team.budget_type not in SUBSCRIPTION_BUDGET_TYPES:
        return

    try:
        snapshot = await fetch_team_spend_snapshot_for_region(
            db=db,
            team=team,
            region=region,
        )
        upsert_team_spend_period(
            db=db,
            team=team,
            region_id=region.id,
            period_start=period_start,
            period_end=period_end,
            source="moad_subscription_cycle",
            snapshot=snapshot,
            stripe_event_id=source_event_id,
            stripe_invoice_id=None,
            stripe_subscription_id=None,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            "Failed to capture spend period for team_id=%s region_id=%s: %s",
            team.id,
            region.id,
            str(exc),
        )


async def _sync_periodic_ledger_for_invoice(
    *,
    db: Session,
    customer_id: str,
    invoice_obj,
    source_payment_id: int | None,
    region_id: int | None,
) -> None:
    # Ledger allocation is invoice-driven, not real-time. We reconcile the latest
    # spend snapshot per billing period; therefore consumed_cents is eventually
    # consistent between invoices, not a live spend counter.
    team = db.query(DBTeam).filter(DBTeam.stripe_customer_id == customer_id).first()
    if not team or team.budget_type not in SUBSCRIPTION_BUDGET_TYPES:
        return

    period_start_ts = getattr(invoice_obj, "period_start", None)
    period_end_ts = getattr(invoice_obj, "period_end", None)
    raw_amount_paid = getattr(invoice_obj, "amount_paid", 0)
    try:
        amount_paid = int(raw_amount_paid or 0)
    except (TypeError, ValueError):
        amount_paid = 0
    if period_start_ts is None or period_end_ts is None:
        return
    period_start = datetime.fromtimestamp(period_start_ts, tz=UTC)
    period_end = datetime.fromtimestamp(period_end_ts, tz=UTC)

    if region_id is None:
        logger.warning(
            "Skipping periodic ledger sync: missing region_id for periodic team_id=%s",
            team.id,
        )
        return
    region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not region:
        logger.warning(
            "Skipping periodic ledger sync: region_id=%s not found for periodic team_id=%s",
            region_id,
            team.id,
        )
        return

    snapshot = await fetch_team_spend_snapshot_for_region(
        db=db, team=team, region=region
    )
    snapshot_total_spend = (
        snapshot.get("total_spend", 0.0)
        if isinstance(snapshot, dict)
        else getattr(snapshot, "total_spend", 0.0)
    )
    spend_cents = int(round(float(snapshot_total_spend) * 100))
    allocate_period_spend_fifo(
        db, team_id=team.id, region_id=region.id, spend_cents=spend_cents
    )
    materialize_topup_rollovers(
        db,
        team_id=team.id,
        region_id=region.id,
        source_invoice_id=getattr(invoice_obj, "id", None),
        rollover_at=period_end,
    )
    expire_subscription_entries(
        db, team_id=team.id, region_id=region.id, period_end=period_end
    )
    add_subscription_entry(
        db,
        team_id=team.id,
        region_id=region.id,
        amount_cents=amount_paid,
        purchased_at=period_start,
        period_start=period_start,
        period_end=period_end,
        source_payment_id=source_payment_id,
        source_invoice_id=getattr(invoice_obj, "id", None),
    )


async def _sync_periodic_ledger_for_period(
    *,
    db: Session,
    team: DBTeam,
    region: DBRegion,
    period_start: datetime,
    period_end: datetime,
    amount_cents: int,
    source_payment_id: int | None,
    source_invoice_id: str | None,
) -> None:
    if team.budget_type not in SUBSCRIPTION_BUDGET_TYPES:
        return

    try:
        litellm_service = LiteLLMService(
            api_url=region.litellm_api_url, api_key=region.litellm_api_key
        )
        lite_team_id = LiteLLMService.format_team_id(region.name, team.id)
        team_info_resp = await litellm_service.get_team_info(lite_team_id)
        team_info = team_info_resp.get("team_info", team_info_resp)
        snapshot_total_spend = float(team_info.get("spend", 0.0) or 0.0)
    except Exception:
        snapshot = await fetch_team_spend_snapshot_for_region(
            db=db, team=team, region=region
        )
        snapshot_total_spend = (
            snapshot.get("total_spend", 0.0)
            if isinstance(snapshot, dict)
            else getattr(snapshot, "total_spend", 0.0)
        )

    spend_cents = int(round(float(snapshot_total_spend) * 100))
    allocate_period_spend_fifo(
        db, team_id=team.id, region_id=region.id, spend_cents=spend_cents
    )
    materialize_topup_rollovers(
        db,
        team_id=team.id,
        region_id=region.id,
        source_invoice_id=source_invoice_id,
        rollover_at=period_end,
    )
    expire_subscription_entries(
        db, team_id=team.id, region_id=region.id, period_end=period_end
    )
    add_subscription_entry(
        db,
        team_id=team.id,
        region_id=region.id,
        amount_cents=amount_cents,
        purchased_at=period_start,
        period_start=period_start,
        period_end=period_end,
        source_payment_id=source_payment_id,
        source_invoice_id=source_invoice_id,
    )


async def reconcile_periodic_team_budget_drift(
    *, db: Session, team: DBTeam, region: DBRegion
) -> BudgetDriftResult | None:
    if team.budget_type not in SUBSCRIPTION_BUDGET_TYPES:
        return None
    service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    lite_team_id = LiteLLMService.format_team_id(region.name, team.id)
    team_info_resp = await service.get_team_info(lite_team_id)
    team_info = team_info_resp.get("team_info", team_info_resp)
    current_spend = float(team_info.get("spend", 0.0) or 0.0)
    actual_max_budget = float(team_info.get("max_budget", 0.0) or 0.0)
    topup_remaining_dollars = (
        compute_active_topup_remaining(db, team_id=team.id, region_id=region.id) / 100.0
    )
    sub_remaining_cents = 0
    now_utc = datetime.now(UTC)
    active_subscriptions = (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == team.id,
            DBPeriodicBudgetLedgerEntry.region_id == region.id,
            DBPeriodicBudgetLedgerEntry.entry_type == "subscription",
            DBPeriodicBudgetLedgerEntry.is_active.is_(True),
            (
                DBPeriodicBudgetLedgerEntry.expires_at.is_(None)
                | (DBPeriodicBudgetLedgerEntry.expires_at > now_utc)
            ),
        )
        .all()
    )
    for row in active_subscriptions:
        sub_remaining_cents += max(0, row.amount_cents - row.consumed_cents)
    sub_remaining_dollars = sub_remaining_cents / 100.0
    expected_max_budget = (
        current_spend + sub_remaining_dollars + topup_remaining_dollars
    )
    expected_max_budget_cents = int(round(expected_max_budget * 100))
    actual_max_budget_cents = int(round(actual_max_budget * 100))
    drift_cents = actual_max_budget_cents - expected_max_budget_cents
    return BudgetDriftResult(
        expected_max_budget_cents=expected_max_budget_cents,
        actual_max_budget_cents=actual_max_budget_cents,
        drift_cents=drift_cents,
    )


async def apply_billing_cycle_for_team(
    db: Session,
    team_id: int,
    budget_cents: int,
    region_id: int,
    period_start: datetime,
    period_end: datetime,
    source_payment_id: Optional[int] = None,
) -> list[str]:
    sync_errors: list[str] = []
    try:
        team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
        if not team:
            logger.error(f"Team not found for team ID: {team_id}")
            return sync_errors
        if team.budget_type not in SUBSCRIPTION_BUDGET_TYPES:
            raise ValueError(
                f"Team {team_id} budget_type={team.budget_type} does not support subscription cycles"
            )

        region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
        if not region:
            logger.warning(
                "Skipping billing cycle sync for team %s: region %s not found",
                team.id,
                region_id,
            )
            return sync_errors

        limit_service = LimitService(db)
        _, _, max_rpm_limit = limit_service.get_token_restrictions(team.id)
        per_region_budget = budget_cents / 100.0
        # Safety-net: Stripe cycles are 30d. The 31d budget_duration on LiteLLM
        # auto-expires budget if a webhook is missed. On cancellation, Stripe sends
        # customer.subscription.deleted which handles explicit cleanup.
        budget_duration = "31d"
        keys = get_team_region_litellm_keys(db, team_id=team.id, region_id=region.id)

        litellm_service = LiteLLMService(
            api_url=region.litellm_api_url, api_key=region.litellm_api_key
        )
        lite_team_id = LiteLLMService.format_team_id(region.name, team.id)

        team_max_budget = per_region_budget
        current_team_spend = 0.0
        try:
            team_info_resp = await litellm_service.get_team_info(lite_team_id)
            team_info = team_info_resp.get("team_info", team_info_resp)
            current_team_spend = float(team_info.get("spend", 0.0) or 0.0)

            # DB ledger is the source of truth for actual remaining budget.
            now_utc = datetime.now(UTC)
            active_subscriptions = (
                db.query(DBPeriodicBudgetLedgerEntry)
                .filter(
                    DBPeriodicBudgetLedgerEntry.team_id == team.id,
                    DBPeriodicBudgetLedgerEntry.region_id == region.id,
                    DBPeriodicBudgetLedgerEntry.entry_type == "subscription",
                    DBPeriodicBudgetLedgerEntry.is_active.is_(True),
                    (
                        DBPeriodicBudgetLedgerEntry.expires_at.is_(None)
                        | (DBPeriodicBudgetLedgerEntry.expires_at > now_utc)
                    ),
                )
                .all()
            )
            sub_remaining_cents = 0
            for row in active_subscriptions:
                sub_remaining_cents += max(0, row.amount_cents - row.consumed_cents)
            topup_remaining_cents = compute_active_topup_remaining(
                db, team_id=team.id, region_id=region.id
            )
            # Normal path: ledger is synced before this call.
            # Safety fallback for direct/test invocations where ledger sync did not run.
            if sub_remaining_cents == 0 and budget_cents > 0:
                sub_remaining_cents = budget_cents
            real_remaining_dollars = (
                sub_remaining_cents + topup_remaining_cents
            ) / 100.0

            # Optional operator team cap (DBSpendCap) constrains enforcement only.
            team_cap = (
                db.query(DBSpendCap.max_budget)
                .filter(
                    DBSpendCap.scope == "team",
                    DBSpendCap.region_id == region.id,
                    DBSpendCap.team_id == team.id,
                )
                .scalar()
            )
            effective_remaining_dollars = (
                min(real_remaining_dollars, float(team_cap))
                if team_cap is not None
                else real_remaining_dollars
            )
            effective_remaining_dollars = max(0.0, effective_remaining_dollars)

            # LiteLLM team spend is non-resettable. Project max_budget as:
            # current_litellm_spend + effective_remaining_budget.
            team_max_budget = current_team_spend + effective_remaining_dollars
            logger.info(
                "Projected team %s budget: litellm_spend=%s + effective_remaining=%s (real_remaining=%s, team_cap=%s) => %s",
                team.id,
                current_team_spend,
                effective_remaining_dollars,
                real_remaining_dollars,
                team_cap,
                team_max_budget,
            )
        except Exception as e:
            error_msg = (
                f"Failed to read team spend / project budget "
                f"(team {team.id}, region {region.name}): {e}"
            )
            logger.error(error_msg)
            sync_errors.append(error_msg)

        if not sync_errors:
            try:
                await litellm_service.update_team_budget(
                    team_id=lite_team_id,
                    max_budget=team_max_budget,
                    budget_duration=budget_duration,
                )
                logger.info(
                    "Updated team %s budget to %s (duration=%s) in region %s",
                    team.id,
                    team_max_budget,
                    budget_duration,
                    region.name,
                )
                if keys:
                    try:
                        drift_result = await reconcile_periodic_team_budget_drift(
                            db=db, team=team, region=region
                        )
                        if drift_result and drift_result.drift_cents != 0:
                            logger.warning(
                                "Periodic budget drift detected team_id=%s region_id=%s expected_max_budget_cents=%s actual_max_budget_cents=%s drift_cents=%s",
                                team.id,
                                region.id,
                                drift_result.expected_max_budget_cents,
                                drift_result.actual_max_budget_cents,
                                drift_result.drift_cents,
                            )
                    except Exception as drift_exc:
                        logger.warning(
                            "Drift reconciliation failed for team_id=%s region_id=%s: %s",
                            team.id,
                            region.id,
                            drift_exc,
                        )
            except Exception as e:
                error_msg = f"Failed to update team {team.id} budget in region {region.name}: {str(e)}"
                logger.error(error_msg)
                sync_errors.append(error_msg)

        if not sync_errors:
            for key in keys:
                try:
                    key_cap_row = (
                        db.query(DBSpendCap.max_budget, DBSpendCap.budget_duration)
                        .filter(
                            DBSpendCap.scope == "key",
                            DBSpendCap.region_id == region.id,
                            DBSpendCap.key_id == key.id,
                        )
                        .first()
                    )
                    key_spend_cap = key_cap_row[0] if key_cap_row else None
                    key_cap_duration = key_cap_row[1] if key_cap_row else None

                    if team.requires_pool_purchase_gate:
                        # POOL: key max_budget must be set only when an explicit key cap exists.
                        # Otherwise keep key max_budget null and enforce at team level.
                        await litellm_service.set_key_restrictions(
                            litellm_token=key.litellm_token,
                            duration=budget_duration,
                            # Keep POOL key windows aligned with team cycle window
                            # even when no explicit key cap exists.
                            budget_duration=(
                                # POOL key caps use 31d windows aligned with cycle semantics.
                                budget_duration
                                if key_spend_cap is not None
                                else budget_duration
                            ),
                            budget_amount=(
                                float(key_spend_cap)
                                if key_spend_cap is not None
                                else None
                            ),
                            rpm_limit=max_rpm_limit,
                            spend=0.0,
                        )
                        logger.info(
                            "Updated POOL key %s limits in LiteLLM: duration=%s, key_cap=%s, key_cap_duration=%s, rpm=%s, spend_reset=True",
                            key.id,
                            budget_duration,
                            key_spend_cap,
                            key_cap_duration,
                            max_rpm_limit,
                        )
                    else:
                        effective_key_budget = (
                            float(key_spend_cap)
                            if key_spend_cap is not None
                            else per_region_budget
                        )
                        await litellm_service.set_key_restrictions(
                            litellm_token=key.litellm_token,
                            duration=budget_duration,
                            budget_duration=budget_duration,
                            budget_amount=effective_key_budget,
                            rpm_limit=max_rpm_limit,
                            spend=0.0,
                        )
                        logger.info(
                            "Updated key %s limits in LiteLLM: duration=%s, budget=%s, rpm=%s, spend_reset=True",
                            key.id,
                            budget_duration,
                            effective_key_budget,
                            max_rpm_limit,
                        )
                except Exception as e:
                    error_msg = f"Failed to update key {key.id} in LiteLLM: {str(e)}"
                    logger.error(error_msg)
                    sync_errors.append(error_msg)

        set_team_and_user_limits(db, team)

        if source_payment_id:
            payment_record = (
                db.query(DBPeriodicPayment)
                .filter(DBPeriodicPayment.id == source_payment_id)
                .first()
            )
            if payment_record:
                if sync_errors:
                    payment_record.sync_status = "sync_failed"
                    payment_record.error_log = "\n".join(sync_errors)
                else:
                    payment_record.sync_status = "success"

        # Only stamp last_payment after successful sync to avoid marking
        # teams as "recently paid" when LiteLLM sync failed.
        if not sync_errors:
            team.last_payment = period_start

        db.commit()
        return sync_errors
    except Exception as e:
        db.rollback()
        logger.error(f"Error applying billing cycle to team: {str(e)}")
        if source_payment_id:
            try:
                payment_record = (
                    db.query(DBPeriodicPayment)
                    .filter(DBPeriodicPayment.id == source_payment_id)
                    .first()
                )
                if payment_record:
                    payment_record.sync_status = "sync_failed"
                    payment_record.error_log = f"Critical failure: {str(e)}"
                    db.commit()
            except Exception as inner_e:
                logger.error(
                    f"Failed to log critical error to payment record: {inner_e}"
                )
        raise


async def apply_product_for_team(
    db: Session,
    customer_id: str,
    product_id: str,
    start_date: datetime,
    payment_record_id: Optional[int] = None,
    region_id: Optional[int] = None,
):
    """Compatibility wrapper for legacy product-based tests and flows."""
    team = db.query(DBTeam).filter(DBTeam.stripe_customer_id == customer_id).first()
    product = db.query(DBProduct).filter(DBProduct.id == product_id).first()

    if not team:
        logger.error(f"Team not found for customer ID: {customer_id}")
        return []
    if not product:
        logger.error(f"Product not found for ID: {product_id}")
        return []

    existing_association = (
        db.query(DBTeamProduct)
        .filter(
            DBTeamProduct.team_id == team.id, DBTeamProduct.product_id == product.id
        )
        .first()
    )
    if not existing_association:
        db.add(DBTeamProduct(team_id=team.id, product_id=product.id))
    db.commit()

    if team.budget_type in SUBSCRIPTION_BUDGET_TYPES:
        period_start = start_date
        # Safety-net: Stripe cycles are 30d. The 31d budget_duration on LiteLLM
        # auto-expires budget if a webhook is missed.
        period_end = start_date + timedelta(days=31)
        budget_cents = int(round((product.max_budget_per_key or 0.0) * 100))
        if region_id is not None:
            return await apply_billing_cycle_for_team(
                db=db,
                team_id=team.id,
                budget_cents=budget_cents,
                region_id=region_id,
                period_start=period_start,
                period_end=period_end,
                source_payment_id=payment_record_id,
            )

        keys_by_region = get_team_keys_by_region(db, team.id)
        if not keys_by_region:
            set_team_and_user_limits(db, team)
            team.last_payment = start_date
            db.commit()
            return []

        all_errors: list[str] = []
        for region in keys_by_region:
            all_errors.extend(
                await apply_billing_cycle_for_team(
                    db=db,
                    team_id=team.id,
                    budget_cents=budget_cents,
                    region_id=region.id,
                    period_start=period_start,
                    period_end=period_end,
                    source_payment_id=payment_record_id,
                )
            )
        return all_errors

    sync_errors = []
    limit_service = LimitService(db)
    days_left_in_period, max_max_spend, max_rpm_limit = (
        limit_service.get_token_restrictions(team.id)
    )
    budget_duration = f"{days_left_in_period}d"

    if region_id is None:
        keys_by_region = get_team_keys_by_region(db, team.id)
    else:
        region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
        if not region:
            logger.warning(
                "Skipping product sync for team %s: region %s not found",
                team.id,
                region_id,
            )
            return []
        region_keys = get_team_region_litellm_keys(
            db, team_id=team.id, region_id=region_id
        )
        keys_by_region = {region: region_keys}

    if not keys_by_region:
        logger.warning(
            "Skipping product sync for team %s: no regions with keys found",
            team.id,
        )
        team.last_payment = start_date
        db.commit()
        return []

    for region, keys in keys_by_region.items():
        litellm_service = LiteLLMService(
            api_url=region.litellm_api_url, api_key=region.litellm_api_key
        )
        lite_team_id = LiteLLMService.format_team_id(region.name, team.id)
        try:
            await litellm_service.update_team_budget(
                team_id=lite_team_id,
                max_budget=max_max_spend,
                budget_duration=budget_duration,
            )
        except Exception as e:
            sync_errors.append(
                f"Failed to update team {team.id} budget in region {region.name}: {str(e)}"
            )

        for key in keys:
            try:
                key_spend_cap = (
                    db.query(DBSpendCap.max_budget)
                    .filter(
                        DBSpendCap.scope == "key",
                        DBSpendCap.region_id == region.id,
                        DBSpendCap.key_id == key.id,
                    )
                    .scalar()
                )
                effective_key_budget = (
                    float(key_spend_cap) if key_spend_cap is not None else max_max_spend
                )
                await litellm_service.set_key_restrictions(
                    litellm_token=key.litellm_token,
                    duration=budget_duration,
                    budget_duration=budget_duration,
                    budget_amount=effective_key_budget,
                    rpm_limit=max_rpm_limit,
                    spend=None,
                )
            except Exception as e:
                sync_errors.append(
                    f"Failed to update key {key.id} in LiteLLM: {str(e)}"
                )

    limit_service.set_team_limits(team)
    if payment_record_id:
        payment_record = (
            db.query(DBPeriodicPayment)
            .filter(DBPeriodicPayment.id == payment_record_id)
            .first()
        )
        if payment_record:
            if sync_errors:
                payment_record.sync_status = "sync_failed"
                payment_record.error_log = "\n".join(sync_errors)
            else:
                payment_record.sync_status = "success"

    # Stamp last_payment after successful sync for non-PERIODIC teams.
    # PERIODIC teams have last_payment set inside apply_billing_cycle_for_team.
    if not sync_errors:
        team.last_payment = start_date
    db.commit()
    return sync_errors


async def reconcile_team_keys(
    db: Session,
    team: DBTeam,
    keys_by_region: Dict[DBRegion, List[DBPrivateAIKey]],
    expire_keys: bool,
    renewal_period_days: Optional[int] = None,
    max_budget_amount: Optional[float] = None,
) -> float:
    """
    Monitor spend for all keys in a team across different regions and optionally update keys after renewal period.

    Args:
        team: The team to monitor keys for
        keys_by_region: Dictionary mapping regions to lists of keys
        expire_keys: Whether to expire keys (set duration to 0)
        renewal_period_days: Optional renewal period in days. If provided, will check for and update keys renewed within the last hour.
        max_budget_amount: Optional maximum budget amount. If provided, will update the budget amount for the keys.

    Returns:
        float: Total spend across all keys for the team
    """
    team_total = 0
    total_by_user = defaultdict(float)
    service_key_total = 0
    current_time = datetime.now(UTC)

    # Monitor keys for each region
    for region, keys in keys_by_region.items():
        try:
            # Initialize LiteLLM service for this region
            litellm_service = LiteLLMService(
                api_url=region.litellm_api_url, api_key=region.litellm_api_key
            )

            # Check spend for each key in this region
            for key in keys:
                try:
                    # Get current spend using get_key_info
                    key_info = await litellm_service.get_key_info(key.litellm_token)
                    info = key_info.get("info", {})
                    # Ensure that even if LiteLLM returns `None` we have a value
                    current_spend = info.get("spend", 0) or 0.0
                    budget = info.get("max_budget", 0) or 0.0
                    key_alias = info.get(
                        "key_alias", f"key-{key.id}"
                    )  # Fallback to key-{id} if no alias

                    # Only update cached_spend and updated_at if spend has actually changed
                    if key.cached_spend != current_spend:
                        key.cached_spend = current_spend
                        key.updated_at = datetime.now(UTC)
                        logger.info(
                            f"Key {key.id} spend updated from {key.cached_spend} to {current_spend}"
                        )
                    if key.owner_id:
                        total_by_user[key.owner_id] += current_spend
                    else:
                        service_key_total += current_spend

                    if expire_keys:
                        logger.info(
                            f"Key {key.id} expiring, setting duration to 0 days"
                        )
                        await litellm_service.update_key_duration(
                            key.litellm_token, "0d"
                        )
                    else:
                        update_data = {"litellm_token": key.litellm_token}
                        needs_update = False
                        current_max_budget = info.get("max_budget")
                        # If the budget amount mis-matches, always update that field
                        if (
                            max_budget_amount is not None
                            and current_max_budget != max_budget_amount
                        ):
                            update_data["budget_amount"] = max_budget_amount
                            needs_update = True
                            logger.info(
                                f"Key {key.id} has incorrect budget of {current_max_budget}, will change to {max_budget_amount}"
                            )

                        current_budget_duration = info.get("budget_duration")
                        # If budget_duration is None, always update it
                        if (
                            current_budget_duration is None
                            and renewal_period_days is not None
                        ):
                            update_data["budget_duration"] = f"{renewal_period_days}d"
                            needs_update = True
                            logger.info(
                                f"Key {key.id} budget update triggered by None budget_duration"
                            )
                        # If budget_duration is "0d", always update it (fix for expired keys)
                        elif (
                            current_budget_duration == "0d"
                            and renewal_period_days is not None
                        ):
                            update_data["budget_duration"] = f"{renewal_period_days}d"
                            needs_update = True
                            logger.info(
                                f"Key {key.id} budget update triggered by 0d duration (expired key fix)"
                            )

                        expiry_date = info.get("expires")
                        if expiry_date and renewal_period_days is not None:
                            parsed_expiry_date = datetime.fromisoformat(
                                expiry_date.replace("Z", "+00:00")
                            )
                            this_month = current_time + timedelta(days=30)
                            if parsed_expiry_date <= this_month:
                                update_data["budget_duration"] = (
                                    f"{renewal_period_days}d"
                                )
                                needs_update = True
                                logger.info(
                                    f"Key {key.id} expires at {expiry_date}, updating to extend life."
                                )

                        if needs_update:
                            # Determine what the new budget_duration will be for logging
                            new_budget_duration = update_data.get(
                                "budget_duration", current_budget_duration
                            )
                            logger.info(
                                f"Key {key.id} budget update triggered: changing from {current_budget_duration}, {current_max_budget} to {new_budget_duration}, {max_budget_amount}"
                            )

                            # Extract litellm_token and other parameters for update_budget call
                            litellm_token = update_data.pop("litellm_token")
                            budget_duration = update_data.get("budget_duration")
                            budget_amount = update_data.get("budget_amount")

                            # Call update_budget with correct parameter order
                            # Always pass budget_duration as second positional argument (can be None)
                            await litellm_service.update_budget(
                                litellm_token,
                                budget_duration,
                                budget_amount=budget_amount,
                            )
                            logger.info(f"Updated key {key.id} budget settings")
                        else:
                            logger.info(
                                f"Key {key.id} budget settings already match the expected values, no update needed"
                            )

                    # Add to team total
                    team_total += current_spend

                    # Calculate and post percentage used
                    if budget > 0:
                        percentage_used = (current_spend / budget) * 100
                        key_spend_percentage.labels(
                            team_id=str(team.id),
                            team_name=team.name,
                            key_alias=key_alias,
                        ).set(percentage_used)

                        # Log warning if approaching limit
                        if percentage_used >= 80:
                            logger.warning(
                                f"Key {key_alias} for team {team.name} is approaching spend limit: "
                                f"${current_spend:.2f} of ${budget:.2f} ({percentage_used:.1f}%)"
                            )
                    else:
                        # Set to 0 if no budget is set
                        key_spend_percentage.labels(
                            team_id=str(team.id),
                            team_name=team.name,
                            key_alias=key_alias,
                        ).set(0)

                except Exception as e:
                    logger.error(f"Error monitoring key {key.id} spend: {str(e)}")
                    continue

        except Exception as e:
            logger.error(
                f"Error initializing LiteLLM service for region {region.name}: {str(e)}"
            )
            continue

    limit_service = LimitService(db)
    for user_id in total_by_user.keys():
        limit = (
            db.query(DBLimitedResource)
            .filter(
                and_(
                    DBLimitedResource.owner_type == OwnerType.USER,
                    DBLimitedResource.owner_id == user_id,
                    DBLimitedResource.resource == ResourceType.BUDGET,
                )
            )
            .first()
        )
        if limit:
            limit_schema = LimitedResource.model_validate(limit)
            limit_service.set_current_value(limit_schema, total_by_user[user_id])

    service_key_limit = (
        db.query(DBLimitedResource)
        .filter(
            and_(
                DBLimitedResource.owner_type == OwnerType.TEAM,
                DBLimitedResource.owner_id == team.id,
                DBLimitedResource.resource == ResourceType.BUDGET,
            )
        )
        .first()
    )
    if service_key_limit:
        limit_schema = LimitedResource.model_validate(service_key_limit)
        limit_service.set_current_value(limit_schema, service_key_total)

    return team_total


def _monitor_team_freshness(team: DBTeam, db: Optional[Session] = None) -> int:
    current_time = datetime.now(UTC)
    # Calculate team age based on whether they have made a payment
    # For pool teams, query DBPoolPurchase directly (single source of truth) so that
    # this function and the has_active_pool_purchase check in monitor_teams are consistent.
    pool_purchase_date = None
    if getattr(team, "budget_type", None) == BudgetType.POOL:
        if db is not None:
            latest = (
                db.query(DBPoolPurchase)
                .filter(DBPoolPurchase.team_id == team.id)
                .order_by(DBPoolPurchase.purchased_at.desc())
                .first()
            )
            if latest:
                pool_purchase_date = latest.purchased_at
        elif team.last_pool_purchase:
            # Fallback when no db session is available (e.g. unit tests that don't pass db)
            pool_purchase_date = team.last_pool_purchase

    if pool_purchase_date:
        team_freshness = (current_time - pool_purchase_date.replace(tzinfo=UTC)).days
    elif team.last_payment:
        team_freshness = (current_time - team.last_payment.replace(tzinfo=UTC)).days
    else:
        team_freshness = (current_time - team.created_at.replace(tzinfo=UTC)).days

    if team_freshness < 0:
        logger.warning(
            f"Team {team.name} (ID: {team.id}) has a negative age: {team_freshness} days"
        )
        team_freshness = 0

    # Post freshness metric (always emit metrics)
    team_freshness_days.labels(team_id=str(team.id), team_name=team.name).set(
        team_freshness
    )

    return team_freshness


def _calculate_last_team_activity(db: Session, team: DBTeam) -> Optional[datetime]:
    """
    Calculate the last activity date for a team based on:
    - Any product association (team is active if any product exists)
    - Most recent key updated_at (indicates usage)
    - Most recent user created_at
    - Most recent key created_at

    Args:
        db: Database session
        team: The team to check

    Returns:
        The most recent activity date, or None if no activity found
    """
    # Check if team has any product associations - if so, team is active
    has_products = (
        db.query(DBTeamProduct).filter(DBTeamProduct.team_id == team.id).first()
        is not None
    )

    if has_products:
        # Team has products, return current time to indicate team is active
        return datetime.now(UTC)

    activity_dates = []

    # Check most recent key update (usage indicator)
    recent_key_update = (
        db.query(DBPrivateAIKey)
        .filter(
            DBPrivateAIKey.team_id == team.id, DBPrivateAIKey.updated_at.isnot(None)
        )
        .order_by(DBPrivateAIKey.updated_at.desc())
        .first()
    )

    if recent_key_update:
        activity_dates.append(recent_key_update.updated_at)

    # Check most recent user creation
    recent_user = (
        db.query(DBUser)
        .filter(DBUser.team_id == team.id)
        .order_by(DBUser.created_at.desc())
        .first()
    )

    if recent_user:
        activity_dates.append(recent_user.created_at)

    # Check most recent key creation
    recent_key_creation = (
        db.query(DBPrivateAIKey)
        .filter(DBPrivateAIKey.team_id == team.id)
        .order_by(DBPrivateAIKey.created_at.desc())
        .first()
    )

    if recent_key_creation:
        activity_dates.append(recent_key_creation.created_at)

    # Return the most recent activity date
    return max(activity_dates) if activity_dates else None


async def _check_team_retention_policy(
    db: Session, team: DBTeam, current_time: datetime, ses_service: Optional[SESService]
) -> None:
    """
    Check and apply team retention policy for inactive teams.

    Uses centralized soft_delete_team() service function for consistency.

    Args:
        db: Database session
        team: The team to check
        current_time: Current timestamp
        ses_service: SES service instance for sending emails
    """
    # POOL teams are always considered active per product policy.
    # They are excluded from inactivity-based retention checks.
    if team.budget_type == BudgetType.POOL:
        return

    # Check team retention policy (only for non-deleted teams)
    if team.deleted_at:
        return  # Team already soft-deleted, skip retention check

    last_activity = _calculate_last_team_activity(db, team)

    if last_activity:
        days_since_activity = (current_time - last_activity.replace(tzinfo=UTC)).days
    else:
        # No activity found, use team creation date
        days_since_activity = (current_time - team.created_at.replace(tzinfo=UTC)).days

    # Emit activity metric
    team_days_since_activity.labels(team_id=str(team.id), team_name=team.name).set(
        days_since_activity
    )

    # Check if team should get a warning (>76 days inactive AND no warning sent)
    should_get_warning = days_since_activity > 76 and not team.retention_warning_sent_at

    # Check if team should be deleted (warning was sent AND 14+ days have passed)
    should_delete = team.retention_warning_sent_at is not None
    if should_delete:
        days_since_warning = (
            current_time - team.retention_warning_sent_at.replace(tzinfo=UTC)
        ).days
        should_delete = days_since_warning >= 14

    if should_delete:
        logger.info(
            f"Team {team.id} ({team.name}) has been inactive for {days_since_activity} days and warning was sent {days_since_warning} days ago, soft-deleting team"
        )

        # Use centralized soft_delete_team service function
        await soft_delete_team(db, team, current_time)

        # Emit deletion metric
        team_retention_deleted_total.labels(
            team_id=str(team.id), team_name=team.name
        ).inc()

        logger.info(
            f"Soft-deleted team {team.id} ({team.name}) due to {days_since_activity} days of inactivity and 14+ days since warning"
        )
        return

    elif should_get_warning:
        logger.info(
            f"Team {team.id} ({team.name}) has been inactive for {days_since_activity} days, sending retention warning"
        )
        _send_retention_warning(db, team, ses_service)
    elif days_since_activity <= 76 and team.retention_warning_sent_at:
        # Team has become active again (within 76 days), reset warning timestamp
        logger.info(
            f"Team {team.id} ({team.name}) has become active again, resetting retention warning timestamp"
        )
        team.retention_warning_sent_at = None
        db.commit()


def _send_retention_warning(
    db: Session, team: DBTeam, ses_service: Optional[SESService]
) -> None:
    """
    Send retention warning email to team admin.

    Args:
        db: Database session
        team: The team to send warning to
        ses_service: SES service instance
    """
    if not ses_service:
        logger.warning(
            f"Cannot send retention warning for team {team.id} - SES service not available"
        )
        return

    try:
        # Send retention warning email
        soft_delete_date = (datetime.now(UTC) + timedelta(days=14)).strftime(
            "%b %d, %Y"
        )
        template_data = {"name": team.name, "soft_delete_date": soft_delete_date}

        success = ses_service.send_email(
            to_addresses=[team.admin_email],
            template_name="team-retention-warning",
            template_data=template_data,
        )

        if success:
            # Update warning sent timestamp
            team.retention_warning_sent_at = datetime.now(UTC)
            db.commit()

            # Emit metrics
            team_retention_warning_sent_total.labels(
                team_id=str(team.id), team_name=team.name
            ).inc()

            logger.info(f"Sent retention warning email to team {team.id} ({team.name})")
        else:
            logger.error(
                f"Failed to send retention warning email to team {team.id} ({team.name})"
            )

    except Exception as e:
        logger.error(
            f"Error sending retention warning email to team {team.id}: {str(e)}"
        )


def _send_expiry_notification(
    db: Session,
    team: DBTeam,
    has_products: bool,
    should_send_notifications: bool,
    days_remaining: int,
    ses_service: Optional[SESService],
):
    # Check for notification conditions for teams still in the trial (only if not recently monitored)
    if not has_products and should_send_notifications:
        # Find the admin email for the team
        try:
            admin_email = get_team_admin_email(db, team)
        except ValueError:
            logger.warning(
                f"No admin user found for team {team.name} (ID: {team.id}), skipping email notifications"
            )
            admin_email = None

        if (
            days_remaining == FIRST_EMAIL_DAYS_LEFT
            or days_remaining == SECOND_EMAIL_DAYS_LEFT
        ):
            logger.info(
                f"Team {team.name} (ID: {team.id}) is approaching expiration in {days_remaining} days"
            )
            # Send expiration notification email
            try:
                if admin_email and ses_service:
                    template_data = {
                        "name": team.name,
                        "days_remaining": days_remaining,
                    }
                    ses_service.send_email(
                        to_addresses=[admin_email],
                        template_name="team-expiring",
                        template_data=template_data,
                    )
                    logger.info(
                        f"Sent expiration notification email to team {team.name} (ID: {team.id})"
                    )
                elif admin_email and not ses_service:
                    logger.warning(
                        f"SES service not available, skipping expiration notification email for team {team.name} (ID: {team.id})"
                    )
                else:
                    logger.warning(
                        f"No email found for team {team.name} (ID: {team.id})"
                    )
            except Exception as e:
                logger.error(
                    f"Failed to send expiration notification email to team {team.name}: {str(e)}"
                )
        elif days_remaining == 0:
            # Send expired email
            try:
                if admin_email and ses_service:
                    template_data = {
                        "name": team.name,
                    }
                    ses_service.send_email(
                        to_addresses=[admin_email],
                        template_name="trial-expired",
                        template_data=template_data,
                    )
                    logger.info(
                        f"Sent expired email to team {team.name} (ID: {team.id})"
                    )
                elif admin_email and not ses_service:
                    logger.warning(
                        f"SES service not available, skipping expired email for team {team.name} (ID: {team.id})"
                    )
                else:
                    logger.warning(
                        f"No email found for team {team.name} (ID: {team.id})"
                    )
            except Exception as e:
                logger.error(
                    f"Failed to send expired email to team {team.name}: {str(e)}"
                )
        elif days_remaining <= 0:
            # Post expired metric
            team_expired_metric.labels(team_id=str(team.id), team_name=team.name).inc()


async def reconcile_team_product_associations(db: Session, team: DBTeam):
    """
    Reconcile team product associations with Stripe subscriptions.

    This function ensures that the team's product associations in the database
    match what they are actually subscribed to in Stripe.

    Args:
        db: Database session
        team: The team to reconcile
    """
    if not team.stripe_customer_id:
        logger.info(
            f"Team {team.id} has no stripe_customer_id, skipping product reconciliation"
        )
        return

    try:
        # Get current subscriptions from Stripe
        stripe_subscriptions = await get_subscribed_products_for_customer(
            team.stripe_customer_id
        )
        stripe_product_ids = {product_id for _, product_id in stripe_subscriptions}

        # Get current product associations in database
        current_associations = (
            db.query(DBTeamProduct).filter(DBTeamProduct.team_id == team.id).all()
        )
        current_product_ids = {assoc.product_id for assoc in current_associations}

        logger.info(
            f"Team {team.id}: Stripe products {stripe_product_ids}, DB products {current_product_ids}"
        )

        # Add missing products (in Stripe but not in DB)
        for product_id in stripe_product_ids - current_product_ids:
            # Verify the product exists in our database
            product = db.query(DBProduct).filter(DBProduct.id == product_id).first()
            if product:
                team_product = DBTeamProduct(team_id=team.id, product_id=product_id)
                db.add(team_product)
                logger.info(f"Added product {product_id} to team {team.id}")
            else:
                logger.warning(
                    f"Product {product_id} found in Stripe but not in database for team {team.id}"
                )

        # Remove extra products (in DB but not in Stripe)
        for assoc in current_associations:
            if assoc.product_id not in stripe_product_ids:
                db.delete(assoc)
                logger.info(f"Removed product {assoc.product_id} from team {team.id}")

        # Commit the changes
        db.commit()

    except Exception as e:
        logger.error(
            f"Error reconciling product associations for team {team.id}: {str(e)}"
        )
        db.rollback()
        raise e


@monitor_teams_duration.time()
async def monitor_teams(db: Session):
    """
    Daily monitoring task for teams that:
    1. Posts age metrics for teams (since creation for teams without products, since last payment for teams with products)
    2. Sends notifications for teams approaching expiration (25-30 days)
    3. Posts metrics for expired teams (>30 days)
    4. Monitors key spend and notifies if approaching limits
    """
    logger.info("Monitoring teams")
    try:
        # Get all non-deleted teams
        teams = db.query(DBTeam).filter(DBTeam.deleted_at.is_(None)).all()
        current_time = datetime.now(UTC)

        # Track current active team labels
        current_team_labels = set()
        try:
            # Initialize SES service
            ses_service = SESService()
        except Exception as e:
            logger.error(f"Error initializing SES service: {str(e)}")
            ses_service = None

        logger.info(f"Found {len(teams)} teams to track")
        limit_service = LimitService(db)
        for team in teams:
            try:
                team_label = (str(team.id), team.name)
                current_team_labels.add(team_label)

                # Reconcile product associations with Stripe, skipping only purchase-gated
                # POOL teams which follow a separate purchase-gated lifecycle.
                if not team.requires_pool_purchase_gate:
                    await reconcile_team_product_associations(db, team)

                # Check if team has any products
                has_products = (
                    db.query(DBTeamProduct)
                    .filter(DBTeamProduct.team_id == team.id)
                    .first()
                    is not None
                )

                # Check team retention policy first (soft-delete handles key expiration internally)
                await _check_team_retention_policy(db, team, current_time, ses_service)

                # Now handle trial expiry notifications and key expiry (after retention checks)
                is_pool_team = team.budget_type == BudgetType.POOL

                # Check if team was monitored within 24 hours
                should_send_notifications = settings.ENABLE_LIMITS
                if team.last_monitored:
                    hours_since_monitored = (
                        current_time - team.last_monitored.replace(tzinfo=UTC)
                    ).total_seconds() / 3600
                    should_send_notifications = hours_since_monitored >= 24

                # Always compute freshness to emit team_freshness_days metrics for all teams.
                # POOL teams have their own lifecycle and are excluded from trial notifications.
                team_freshness = _monitor_team_freshness(team, db)
                days_remaining = TRIAL_OVER_DAYS - team_freshness
                if not is_pool_team:
                    _send_expiry_notification(
                        db,
                        team,
                        has_products,
                        should_send_notifications,
                        days_remaining,
                        ses_service,
                    )

                # Get all keys for the team grouped by region
                keys_by_region = get_team_keys_by_region(db, team.id)
                expire_keys = False

                # Expire if team trial has expired (if team has a product, expiry will be handled by Stripe)
                # POOL teams are always exempt from trial expiration.
                if (
                    not has_products
                    and not is_pool_team
                    and days_remaining <= 0
                    and should_send_notifications
                ):
                    logger.info(
                        f"Team {team.id} has {days_remaining} days remaining, expiring keys"
                    )
                    expire_keys = True

                # Determine if we should check for renewal period updates
                renewal_period_days = None
                max_budget_amount = None
                if has_products and team.last_payment:
                    # Get budget from active limits (source of truth)
                    team_limits = limit_service.get_team_limits(team)
                    budget_limit = next(
                        (
                            limit
                            for limit in team_limits
                            if limit.resource == ResourceType.BUDGET
                        ),
                        None,
                    )
                    if budget_limit and not team.requires_pool_purchase_gate:
                        max_budget_amount = budget_limit.max_value

                    # Get the product with the longest renewal period (renewal period not stored in limits)
                    active_products = (
                        db.query(DBTeamProduct)
                        .filter(DBTeamProduct.team_id == team.id)
                        .all()
                    )
                    product_ids = [tp.product_id for tp in active_products]
                    products = (
                        db.query(DBProduct).filter(DBProduct.id.in_(product_ids)).all()
                    )

                    if products:
                        max_renewal_product = max(
                            products, key=lambda product: product.renewal_period_days
                        )
                        renewal_period_days = max_renewal_product.renewal_period_days

                # Monitor keys and get total spend (includes renewal period updates if applicable)
                team_total = await reconcile_team_keys(
                    db,
                    team,
                    keys_by_region,
                    expire_keys,
                    renewal_period_days,
                    max_budget_amount,
                )

                # Set the total spend metric for the team (always emit metrics)
                team_total_spend.labels(team_id=str(team.id), team_name=team.name).set(
                    team_total
                )

                # Update or create team metrics record
                regions_list = list(keys_by_region.keys())
                region_names = [region.name for region in regions_list]

                # Check if metrics record exists
                team_metrics = (
                    db.query(DBTeamMetrics)
                    .filter(DBTeamMetrics.team_id == team.id)
                    .first()
                )

                if team_metrics:
                    logger.info(
                        f"metrics last updated at {team_metrics.last_updated}, curent time is {current_time}"
                    )
                    # Update existing metrics
                    team_metrics.total_spend = team_total
                    team_metrics.last_spend_calculation = current_time
                    team_metrics.regions = region_names
                    team_metrics.last_updated = current_time
                else:
                    # Create new metrics record
                    team_metrics = DBTeamMetrics(
                        team_id=team.id,
                        total_spend=team_total,
                        last_spend_calculation=current_time,
                        regions=region_names,
                        last_updated=current_time,
                    )
                    db.add(team_metrics)

                # Ensure all limits are correct - will not override MANUAL limits
                set_team_and_user_limits(db, team)

                # Update last_monitored timestamp only if notifications were sent
                if should_send_notifications:
                    team.last_monitored = current_time
            except Exception as error:
                logger.error(
                    f"Unable to process team {team.id} due to {str(error)}, continuing with next team."
                )
                # Record the monitoring failure metric
                error_type = type(error).__name__
                team_monitoring_failed_metric.labels(
                    team_id=str(team.id), team_name=team.name, error_type=error_type
                ).inc()

        # Commit the database changes
        db.commit()

        # Zero out metrics for teams that are no longer active
        for old_label in active_team_labels - current_team_labels:
            team_freshness_days.labels(
                team_id=old_label[0], team_name=old_label[1]
            ).set(0)

        # Update active team labels for next run
        active_team_labels.clear()
        active_team_labels.update(current_team_labels)

    except Exception as e:
        logger.error(f"Error in team monitoring task: {str(e)}")
        raise e


@hard_delete_teams_duration.time()
async def hard_delete_expired_teams(db: Session):
    """
    Hard deletion job for teams that have been soft-deleted beyond the retention period.
    Cascades deletion to all related resources (keys, users, limits, metrics, etc.).
    Runs less frequently than monitor_teams (daily at 3 AM).
    """
    logger.info("Starting hard delete job for expired teams")
    try:
        retention_days = max(
            30, int(os.getenv("TEAM_HARD_DELETE_RETENTION_DAYS", "90"))
        )
        cutoff_date = datetime.now(UTC) - timedelta(days=retention_days)

        # Query all teams that have been soft-deleted beyond the retention period
        teams_to_delete = (
            db.query(DBTeam)
            .filter(DBTeam.deleted_at.is_not(None), DBTeam.deleted_at <= cutoff_date)
            .all()
        )

        logger.info(f"Found {len(teams_to_delete)} teams eligible for hard deletion")

        for team in teams_to_delete:
            try:
                logger.info(
                    f"Hard deleting team {team.id} ({team.name}), soft-deleted on {team.deleted_at}"
                )

                # Get user IDs first (needed for cleaning up user resources)
                team_user_ids = (
                    db.execute(select(DBUser.id).filter(DBUser.team_id == team.id))
                    .scalars()
                    .all()
                )

                # Also capture emails now (needed for user_spend_cache cleanup)
                team_user_emails = (
                    db.execute(select(DBUser.email).filter(DBUser.team_id == team.id))
                    .scalars()
                    .all()
                )

                # 1. Delete team and user limited resources
                db.query(DBLimitedResource).filter(
                    DBLimitedResource.owner_type == OwnerType.TEAM,
                    DBLimitedResource.owner_id == team.id,
                ).delete(synchronize_session=False)
                if team_user_ids:
                    db.query(DBLimitedResource).filter(
                        DBLimitedResource.owner_type == OwnerType.USER,
                        DBLimitedResource.owner_id.in_(team_user_ids),
                    ).delete(synchronize_session=False)
                logger.info(
                    f"Deleted limited resources for team {team.id} and its users"
                )

                # 2. Delete keys from LiteLLM and database (use helper to group by region)
                keys_by_region = get_team_keys_by_region(db, team.id)

                # Delete from LiteLLM first
                for region, region_keys in keys_by_region.items():
                    try:
                        litellm_service = LiteLLMService(
                            api_url=region.litellm_api_url,
                            api_key=region.litellm_api_key,
                        )
                        for key in region_keys:
                            if key.litellm_token:
                                try:
                                    await litellm_service.delete_key(key.litellm_token)
                                    logger.info(
                                        f"Deleted key {key.id} from LiteLLM in region {region.name}"
                                    )
                                except Exception as key_error:
                                    logger.error(
                                        f"Failed to delete key {key.id} from LiteLLM: {str(key_error)}"
                                    )
                    except Exception as region_error:
                        logger.error(
                            f"Failed to delete keys from region {region.name}: {str(region_error)}"
                        )

                # Delete keys from database
                # Collect key IDs first so we can clean up spend_caps that reference them
                team_key_ids = (
                    db.execute(
                        select(DBPrivateAIKey.id).filter(
                            or_(
                                DBPrivateAIKey.team_id == team.id,
                                DBPrivateAIKey.owner_id.in_(team_user_ids),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )

                # 3. Delete spend_caps before keys/users/team to avoid FK violations:
                #    spend_caps.key_id → ai_tokens.id (no ondelete)
                #    spend_caps.user_id → users.id (no ondelete)
                #    spend_caps.team_id → teams.id (no ondelete)
                db.query(DBSpendCap).filter(
                    or_(
                        DBSpendCap.team_id == team.id,
                        DBSpendCap.user_id.in_(team_user_ids),
                        DBSpendCap.key_id.in_(team_key_ids),
                    )
                ).delete(synchronize_session=False)
                logger.info(f"Deleted spend caps for team {team.id}")

                total_keys = sum(len(keys) for keys in keys_by_region.values())
                db.query(DBPrivateAIKey).filter(
                    (DBPrivateAIKey.team_id == team.id)
                    | (DBPrivateAIKey.owner_id.in_(team_user_ids))
                ).delete(synchronize_session=False)
                logger.info(
                    f"Deleted {total_keys} keys from database for team {team.id}"
                )

                # 4. Clean up remaining FK references to users before deleting them.
                #    These tables have FKs to users.id with no ondelete rule, so
                #    PostgreSQL would raise a FK violation without explicit cleanup.

                # api_tokens.user_id → users.id (no ondelete)
                if team_user_ids:
                    db.query(DBAPIToken).filter(
                        DBAPIToken.user_id.in_(team_user_ids)
                    ).delete(synchronize_session=False)
                    logger.info(f"Deleted API tokens for users of team {team.id}")

                # user_admin_regions.user_id → users.id (no ondelete, PK)
                if team_user_ids:
                    db.query(DBUserAdminRegion).filter(
                        DBUserAdminRegion.user_id.in_(team_user_ids)
                    ).delete(synchronize_session=False)
                    logger.info(
                        f"Deleted admin-region rows for users of team {team.id}"
                    )

                # audit_logs.user_id → users.id (nullable, no ondelete)
                # Preserve audit history; just null out the user reference.
                if team_user_ids:
                    db.execute(
                        sa_update(DBAuditLog)
                        .where(DBAuditLog.user_id.in_(team_user_ids))
                        .values(user_id=None)
                    )
                    logger.info(
                        f"Nulled user_id in audit logs for users of team {team.id}"
                    )

                # user_spend_cache is keyed by normalized_email (no FK, string column)
                # Clean up stale cache rows so they don't linger indefinitely.
                if team_user_emails:
                    normalized_team_user_emails = {
                        normalize_email_for_lookup(email) for email in team_user_emails
                    }
                    db.query(DBUserSpendCache).filter(
                        DBUserSpendCache.normalized_email.in_(
                            normalized_team_user_emails
                        )
                    ).delete(synchronize_session=False)
                    logger.info(
                        f"Deleted spend cache entries for users of team {team.id}"
                    )

                # 5. Delete users in the team
                db.query(DBUser).filter(DBUser.team_id == team.id).delete()
                logger.info(f"Deleted {len(team_user_ids)} users for team {team.id}")

                # 6. Delete team product associations
                db.query(DBTeamProduct).filter(
                    DBTeamProduct.team_id == team.id
                ).delete()
                logger.info(f"Deleted product associations for team {team.id}")

                # 7. Delete team region associations
                db.query(DBTeamRegion).filter(DBTeamRegion.team_id == team.id).delete()
                logger.info(f"Deleted region associations for team {team.id}")

                # 8. Write audit log before deleting the team record
                hard_delete_time = datetime.now(UTC)
                audit_log = DBAuditLog(
                    timestamp=hard_delete_time,
                    user_id=None,
                    event_type="WORKER",
                    resource_type="team",
                    resource_id=str(team.id),
                    action="team.hard_delete",
                    details={
                        "team_name": team.name,
                        "soft_deleted_at": team.deleted_at.isoformat()
                        if team.deleted_at
                        else None,
                        "hard_deleted_at": hard_delete_time.isoformat(),
                    },
                    request_source=None,
                )
                db.add(audit_log)

                # 9. Delete the team itself (DBTeamMetrics will be auto-deleted via cascade)
                db.delete(team)

                # Commit after each team to avoid rolling back everything on error
                db.commit()

                # Emit metric
                team_hard_deleted_total.labels(
                    team_id=str(team.id), team_name=team.name
                ).inc()

                logger.info(f"Successfully hard deleted team {team.id} ({team.name})")

            except Exception as team_error:
                logger.error(f"Failed to hard delete team {team.id}: {str(team_error)}")
                db.rollback()
                # Continue with next team

        logger.info(
            f"Hard delete job completed. Processed {len(teams_to_delete)} teams"
        )

    except Exception as e:
        logger.error(f"Error in hard delete job: {str(e)}")
        db.rollback()
        raise e


def get_team_admin_email(db: Session, team: DBTeam) -> str:
    """
    Find the admin user for a team and return their email.

    Args:
        db: Database session
        team: The team object to find the admin for

    Returns:
        str: The email of the admin user

    Raises:
        ValueError: If no admin user is found for the team
    """
    # Find a team admin user
    admin_user = (
        db.query(DBUser)
        .filter(DBUser.team_id == team.id, DBUser.role == "admin")
        .first()
    )

    if not admin_user:
        raise ValueError(f"No admin user found for team {team.name} (ID: {team.id})")

    return admin_user.email


def generate_token(email: str, validity_hours: int = 24) -> str:
    """
    Generate a JWT token that authorizes the bearer as an administrator.

    Args:
        email: The email address to generate the token for

    Returns:
        str: The generated JWT token
    """

    # Create token payload with admin claims
    payload = {"sub": email, "exp": datetime.now(UTC) + timedelta(hours=validity_hours)}

    # Generate the token
    token = create_access_token(
        data=payload, expires_delta=timedelta(hours=validity_hours)
    )

    return token


def generate_pricing_url(admin_email: str, validity_hours: int = 24) -> str:
    """
    Generate a URL for the pricing page with a JWT token.

    Args:
        db: Database session
        team: The team object to generate the URL for

    Returns:
        str: The generated URL with the JWT token
    """
    # Generate the token
    token = generate_token(admin_email, validity_hours)

    # Get the frontend URL from settings
    base_url = settings.frontend_route
    path = "/upgrade"
    url = urljoin(base_url, path)

    # Add the token as a query parameter
    return f"{url}?token={token}"
