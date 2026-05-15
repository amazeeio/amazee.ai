from datetime import datetime, UTC, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import select, func, and_
from app.db.models import (
    DBTeam,
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
    DBStripeProcessedEvent,
)
from app.schemas.models import BudgetType
from app.db.database import get_db
from app.services.litellm import LiteLLMService
from app.services.ses import SESService
from app.core.team_service import get_team_keys_by_region, soft_delete_team
from app.core.limit_service import LimitService
from app.schemas.limits import ResourceType, UnitType, OwnerType, LimitedResource
import logging
from collections import defaultdict

# get_token_restrictions is now available through LimitService
from app.services.stripe import (
    get_product_id_from_session,
    get_product_id_from_subscription,
    get_subscribed_products_for_customer,
    KNOWN_EVENTS,
    SUBSCRIPTION_SUCCESS_EVENTS,
    SESSION_SUCCESS_EVENTS,
    SESSION_FAILURE_EVENTS,
    SUBSCRIPTION_FAILURE_EVENTS,
    INVOICE_FAILURE_EVENTS,
    INVOICE_SUCCESS_EVENTS,
    SUCCESS_EVENTS,
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

logger = logging.getLogger(__name__)

FIRST_EMAIL_DAYS_LEFT = 7
SECOND_EMAIL_DAYS_LEFT = 5
TRIAL_OVER_DAYS = 30

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


async def handle_stripe_event_background(event):
    """
    Background task to handle Stripe webhook events.
    This runs in a separate thread to avoid blocking the webhook response.
    Creates its own database session to avoid using the request-scoped session.
    """
    # Create a new database session for this background task
    db = next(get_db())
    try:
        event_type = event.type
        if event_type not in KNOWN_EVENTS:
            logger.info(f"Unknown event type: {event_type}")
            return
        raw_event_id = getattr(event, "id", None)
        event_id = (
            raw_event_id if isinstance(raw_event_id, str) and raw_event_id else None
        )
        should_mark_processed = False
        if event_id:
            existing_event = (
                db.query(DBStripeProcessedEvent)
                .filter(DBStripeProcessedEvent.stripe_event_id == event_id)
                .first()
            )
            if existing_event:
                logger.info("Stripe event already processed: %s", event_id)
                return
            should_mark_processed = True

        event_object = event.data.object
        customer_id = event_object.customer
        if not customer_id:
            logger.warning("No customer ID found in event, cannot complete processing")
            return

        # Record the payment for audit and sync tracking
        payment_record_id = None
        if event_type in SUCCESS_EVENTS:
            payment_record_id = await _record_periodic_payment(db, event_object)

        # Success Events
        if event_type in SUBSCRIPTION_SUCCESS_EVENTS:
            # new subscription – subscription objects expose current_period_start/end,
            # not period_start/period_end, so spend capture is handled on invoice events only.
            product_id = await get_product_id_from_subscription(event_object.id)
            start_date = datetime.fromtimestamp(event_object.start_date, tz=UTC)
            await apply_product_for_team(
                db, customer_id, product_id, start_date, payment_record_id
            )
        elif event_type in INVOICE_SUCCESS_EVENTS:
            # subscription renewed
            await capture_periodic_team_spend_for_invoice(
                db=db,
                customer_id=customer_id,
                invoice_obj=event_object,
                stripe_event_id=event_id,
            )
            subscription = getattr(event_object, "subscription", None)
            # Fallback for complex invoice objects
            if not subscription and hasattr(event_object, "parent"):
                try:
                    subscription = event_object.parent.subscription_details.subscription
                except AttributeError as err:
                    logger.debug(
                        "Invoice object missing nested subscription_details.subscription; "
                        "continuing without subscription fallback: %s",
                        err,
                    )

            if subscription:
                product_id = await get_product_id_from_subscription(subscription)
                start_date = datetime.fromtimestamp(
                    getattr(
                        event_object, "period_start", int(datetime.now().timestamp())
                    ),
                    tz=UTC,
                )
                await _sync_periodic_ledger_for_invoice(
                    db=db,
                    customer_id=customer_id,
                    invoice_obj=event_object,
                    source_payment_id=payment_record_id,
                )
                await apply_product_for_team(
                    db, customer_id, product_id, start_date, payment_record_id
                )
        elif event_type in SESSION_SUCCESS_EVENTS:
            # checkout signup/subscription flow
            subscription = getattr(event_object, "subscription", None)
            if subscription:
                product_id = await get_product_id_from_subscription(subscription)
                await apply_product_for_team(
                    db, customer_id, product_id, datetime.now(UTC), payment_record_id
                )

        # Failure Events
        elif event_type in SESSION_FAILURE_EVENTS:
            product_id = await get_product_id_from_session(event_object.id)
            await remove_product_from_team(db, customer_id, product_id)
        elif event_type in SUBSCRIPTION_FAILURE_EVENTS:
            product_id = await get_product_id_from_subscription(event_object.id)
            await remove_product_from_team(db, customer_id, product_id)
        elif event_type in INVOICE_FAILURE_EVENTS:
            # We assume that the invoice is related to a subscription
            subscription = getattr(event_object, "subscription", None)
            if not subscription and hasattr(event_object, "parent"):
                try:
                    subscription = event_object.parent.subscription_details.subscription
                except AttributeError:
                    # Some invoice payloads do not include nested subscription details.
                    # Keep existing behavior (no subscription fallback) but make it explicit.
                    logger.debug(
                        "Invoice event missing parent.subscription_details.subscription; "
                        "skipping subscription-based removal fallback."
                    )

            if subscription:
                product_id = await get_product_id_from_subscription(subscription)
                await remove_product_from_team(db, customer_id, product_id)

        if should_mark_processed and event_id:
            db.add(
                DBStripeProcessedEvent(
                    stripe_event_id=event_id,
                    event_type=event_type,
                )
            )
            db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Error in background event handler: {str(e)}")
    finally:
        db.close()


async def capture_periodic_team_spend_for_invoice(
    *,
    db: Session,
    customer_id: str,
    invoice_obj,
    stripe_event_id: str | None,
) -> None:
    team = db.query(DBTeam).filter(DBTeam.stripe_customer_id == customer_id).first()
    if team is None:
        logger.warning(
            "Skipping spend period capture: no team for customer_id=%s", customer_id
        )
        return
    if team.budget_type != BudgetType.PERIODIC:
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

    keys_by_region = get_team_keys_by_region(db, team.id)
    if not keys_by_region:
        return

    for region in keys_by_region.keys():
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


async def _sync_periodic_ledger_for_invoice(
    *, db: Session, customer_id: str, invoice_obj, source_payment_id: int | None
) -> None:
    # Ledger allocation is invoice-driven, not real-time. We reconcile the latest
    # spend snapshot per billing period; therefore consumed_cents is eventually
    # consistent between invoices, not a live spend counter.
    team = db.query(DBTeam).filter(DBTeam.stripe_customer_id == customer_id).first()
    if not team or team.budget_type != BudgetType.PERIODIC:
        return

    period_start_ts = getattr(invoice_obj, "period_start", None)
    period_end_ts = getattr(invoice_obj, "period_end", None)
    amount_paid = int(getattr(invoice_obj, "amount_paid", 0) or 0)
    if period_start_ts is None or period_end_ts is None:
        return
    period_start = datetime.fromtimestamp(period_start_ts, tz=UTC)
    period_end = datetime.fromtimestamp(period_end_ts, tz=UTC)

    keys_by_region = get_team_keys_by_region(db, team.id)
    for region in keys_by_region.keys():
        snapshot = await fetch_team_spend_snapshot_for_region(
            db=db, team=team, region=region
        )
        spend_cents = int(round(float(snapshot.total_spend) * 100))
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
    db.commit()


async def reconcile_periodic_team_budget_drift(
    *, db: Session, team: DBTeam, region: DBRegion
) -> BudgetDriftResult | None:
    if team.budget_type != BudgetType.PERIODIC:
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
    active_subscriptions = (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == team.id,
            DBPeriodicBudgetLedgerEntry.region_id == region.id,
            DBPeriodicBudgetLedgerEntry.entry_type == "subscription",
            DBPeriodicBudgetLedgerEntry.is_active.is_(True),
        )
        .all()
    )
    for row in active_subscriptions:
        sub_remaining_cents += max(0, row.amount_cents - row.consumed_cents)
    sub_remaining_dollars = sub_remaining_cents / 100.0
    expected_max_budget = current_spend + sub_remaining_dollars + topup_remaining_dollars
    drift = round(actual_max_budget - expected_max_budget, 6)
    return BudgetDriftResult(
        expected_max_budget=expected_max_budget,
        actual_max_budget=actual_max_budget,
        drift=drift,
    )


async def apply_product_for_team(
    db: Session,
    customer_id: str,
    product_id: str,
    start_date: datetime,
    payment_record_id: Optional[int] = None,
):
    """
    Apply a product to a team and update their last payment date.
    Also extends all team keys and sets their max budgets via LiteLLM service.

    Args:
        db: Database session
        customer_id: Stripe customer ID
        product_id: Product ID from the database

    Returns:
        bool: True if update was successful, False otherwise
    """
    sync_errors = []
    logger.info(f"Applying product {product_id} to team {customer_id}")
    try:
        # Find the team and product
        team = db.query(DBTeam).filter(DBTeam.stripe_customer_id == customer_id).first()
        product = db.query(DBProduct).filter(DBProduct.id == product_id).first()

        if not team:
            logger.error(f"Team not found for customer ID: {customer_id}")
            return
        if not product:
            logger.error(f"Product not found for ID: {product_id}")
            return

        # Update the last payment date
        team.last_payment = start_date

        # Check if the product is already active for the team
        existing_association = (
            db.query(DBTeamProduct)
            .filter(
                DBTeamProduct.team_id == team.id, DBTeamProduct.product_id == product.id
            )
            .first()
        )

        # Only create new association if it doesn't exist
        if not existing_association:
            team_product = DBTeamProduct(team_id=team.id, product_id=product.id)
            db.add(team_product)
            db.commit()  # Commit the product association

        limit_service = LimitService(db)
        days_left_in_period, max_max_spend, max_rpm_limit = (
            limit_service.get_token_restrictions(team.id)
        )

        # PERIODIC teams use a fixed 31-day budget duration and compound
        # the team max_budget to keep the effective monthly budget aligned
        # with the Stripe billing cycle. Key spends are reset to 0 on each
        # webhook so that sum(key spends) reflects only the current period.
        is_periodic = team.budget_type == BudgetType.PERIODIC
        budget_duration = "31d" if is_periodic else f"{days_left_in_period}d"

        # Get all keys for the team grouped by region
        keys_by_region = get_team_keys_by_region(db, team.id)

        # Update keys and team budget for each region
        for region, keys in keys_by_region.items():
            # Initialize LiteLLM service for this region
            litellm_service = LiteLLMService(
                api_url=region.litellm_api_url, api_key=region.litellm_api_key
            )

            # Update the team-level budget (shared ceiling for all keys)
            lite_team_id = LiteLLMService.format_team_id(region.name, team.id)

            # For PERIODIC teams, compound the max_budget so that
            # (max_budget - accumulated_spend) equals the monthly cap.
            # get_team_info is the first LiteLLM call in the sync process.
            # If it fails we abort the sync entirely and leave the payment
            # record as pending so a future retry can pick it up.
            team_max_budget = max_max_spend
            if is_periodic and keys:
                try:
                    team_data = await litellm_service.get_team_info(lite_team_id)
                    team_info = team_data.get("team_info", team_data)
                    current_team_spend = float(team_info.get("spend", 0.0) or 0.0)
                    topup_remaining_cents = compute_active_topup_remaining(
                        db, team_id=team.id, region_id=region.id
                    )
                    topup_remaining_dollars = topup_remaining_cents / 100.0
                    desired_remaining = max_max_spend + topup_remaining_dollars
                    team_max_budget = current_team_spend + desired_remaining
                    logger.info(
                        f"Compounding team {team.id} budget: "
                        f"spend={current_team_spend} + cap={max_max_spend} = {team_max_budget}"
                    )
                except Exception as e:
                    error_msg = (
                        f"Failed to read team spend for compounding "
                        f"(team {team.id}, region {region.name}): {e}"
                    )
                    logger.error(error_msg)
                    sync_errors.append(error_msg)
                    # Do not continue with inaccurate data — mark as failed
                    # and let a future sync process retry.
                    break

            try:
                await litellm_service.update_team_budget(
                    team_id=lite_team_id,
                    max_budget=team_max_budget,
                    budget_duration=budget_duration,
                )
                logger.info(
                    f"Updated team {team.id} budget to {team_max_budget} "
                    f"(duration={budget_duration}) in region {region.name}"
                )
            except Exception as e:
                error_msg = f"Failed to update team {team.id} budget in region {region.name}: {str(e)}"
                logger.error(error_msg)
                sync_errors.append(error_msg)

            # Update each key's duration and budget via LiteLLM.
            # For PERIODIC teams, also reset key spend to 0 so that
            # sum(key spends) = current-period spend only.
            for key in keys:
                try:
                    await litellm_service.set_key_restrictions(
                        litellm_token=key.litellm_token,
                        duration=budget_duration,
                        budget_duration=budget_duration,
                        budget_amount=max_max_spend,
                        rpm_limit=max_rpm_limit,
                        spend=0.0 if is_periodic else None,
                    )
                    logger.info(
                        f"Updated key {key.id} limits in LiteLLM: "
                        f"duration={budget_duration}, budget={max_max_spend}, "
                        f"rpm={max_rpm_limit}, spend_reset={is_periodic}"
                    )
                except Exception as e:
                    error_msg = f"Failed to update key {key.id} in LiteLLM: {str(e)}"
                    logger.error(error_msg)
                    sync_errors.append(error_msg)
                    # Continue with other keys even if one fails
                    continue

        # Ensure that limits are updated
        limit_service.set_team_limits(team)

        # Update periodic payment sync status if provided
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

        db.commit()

    except Exception as e:
        db.rollback()
        logger.error(f"Error applying product to team: {str(e)}")
        if payment_record_id:
            # Try to log the outer exception to the payment record in a new transaction
            try:
                # We need a fresh DB session or at least a fresh transaction
                payment_record = (
                    db.query(DBPeriodicPayment)
                    .filter(DBPeriodicPayment.id == payment_record_id)
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
        raise e


async def remove_product_from_team(db: Session, customer_id: str, product_id: str):
    logger.info(f"Removing product {product_id} from team {customer_id}")
    try:
        # Find the team and product
        team = db.query(DBTeam).filter(DBTeam.stripe_customer_id == customer_id).first()
        product = db.query(DBProduct).filter(DBProduct.id == product_id).first()

        if not team:
            logger.error(f"Team not found for customer ID: {customer_id}")
            return
        if not product:
            logger.error(f"Product not found for ID: {product_id}")
            return
        # Check if the product is already active for the team
        existing_association = (
            db.query(DBTeamProduct)
            .filter(
                DBTeamProduct.team_id == team.id, DBTeamProduct.product_id == product.id
            )
            .first()
        )
        if not existing_association:
            logger.error(f"Product {product_id} not found for team {customer_id}")
            return

        # Verify that the subscription is no longer active in Stripe before removing
        try:
            stripe_subscriptions = await get_subscribed_products_for_customer(
                customer_id
            )
            for stripe_subscription_id, stripe_product_id in stripe_subscriptions:
                if stripe_product_id == product_id:
                    logger.warning(
                        f"Product {product_id} is still active in Stripe subscription {stripe_subscription_id}. Not removing from team {customer_id}"
                    )
                    return
        except Exception as stripe_error:
            logger.error(
                f"Error checking Stripe subscription status for customer {customer_id}: {str(stripe_error)}"
            )
            # If we can't check Stripe status, we should not remove the product to be safe
            logger.warning(
                f"Unable to verify Stripe subscription status. Not removing product {product_id} from team {customer_id}"
            )
            return

        # Remove the product association
        db.delete(existing_association)
        limit_service = LimitService(db)
        limit_service.set_team_limits(team)

        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Error removing product from team: {str(e)}")
        raise e


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
                        "dashboard_url": generate_pricing_url(admin_email),
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
                        "dashboard_url": generate_pricing_url(admin_email),
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
    Hard deletion job for teams that have been soft-deleted for 60+ days.
    Cascades deletion to all related resources (keys, users, limits, metrics, etc.).
    Runs less frequently than monitor_teams (daily at 3 AM).
    """
    logger.info("Starting hard delete job for expired teams")
    try:
        # Calculate cutoff date (60 days ago)
        cutoff_date = datetime.now(UTC) - timedelta(days=60)

        # Query all teams that have been soft-deleted for 60+ days
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
                total_keys = sum(len(keys) for keys in keys_by_region.values())
                db.query(DBPrivateAIKey).filter(
                    (DBPrivateAIKey.team_id == team.id)
                    | (DBPrivateAIKey.owner_id.in_(team_user_ids))
                ).delete(synchronize_session=False)
                logger.info(
                    f"Deleted {total_keys} keys from database for team {team.id}"
                )

                # 3. Delete users in the team
                db.query(DBUser).filter(DBUser.team_id == team.id).delete()
                logger.info(f"Deleted {len(team_user_ids)} users for team {team.id}")

                # 4. Delete team product associations
                db.query(DBTeamProduct).filter(
                    DBTeamProduct.team_id == team.id
                ).delete()
                logger.info(f"Deleted product associations for team {team.id}")

                # 5. Delete team region associations
                db.query(DBTeamRegion).filter(DBTeamRegion.team_id == team.id).delete()
                logger.info(f"Deleted region associations for team {team.id}")

                # 6. Delete the team itself (DBTeamMetrics will be auto-deleted via cascade)
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
