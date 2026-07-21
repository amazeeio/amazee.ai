import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.periodic_budget_ledger_service import (
    allocate_period_spend_fifo,
    compute_active_topup_remaining,
)
from app.core.security import get_role_min_system_admin
from app.core.team_service import get_team_region_litellm_keys
from app.core.worker import (
    _previous_period_spend_baseline_cents,
    _record_periodic_payment_direct,
    _sync_periodic_ledger_for_period,
    apply_billing_cycle_for_team,
    capture_periodic_team_spend_for_period,
)
from app.db.database import get_db
from app.db.models import (
    DBAuditLog,
    DBPeriodicBudgetLedgerEntry,
    DBPeriodicPayment,
    DBRegion,
    DBSpendCap,
    DBTeam,
)
from app.schemas.models import (
    BudgetType,
    SubscriptionCycleRequest,
    SubscriptionCycleResponse,
    SubscriptionDeactivateRequest,
    SubscriptionDeactivateResponse,
)
from app.services.litellm import LiteLLMService

router = APIRouter()
logger = logging.getLogger(__name__)


def _write_audit_log(
    db: Session,
    event_type: str,
    action: str,
    resource_id: str,
    status_code: int,
    details: dict,
):
    try:
        log = DBAuditLog(
            event_type=event_type,
            resource_type="team",
            resource_id=resource_id,
            action=action,
            user_id=None,
            details={**details, "status_code": status_code},
            request_source="api",
        )
        db.add(log)
        db.commit()
    except Exception as exc:
        logger.error("Failed to write audit log: %s", exc)
        try:
            db.rollback()
        except Exception as rollback_exc:
            logger.warning("Failed to rollback audit log transaction: %s", rollback_exc)


@router.post(
    "/cycle",
    response_model=SubscriptionCycleResponse,
    dependencies=[Depends(get_role_min_system_admin)],
)
async def subscription_cycle(
    request: SubscriptionCycleRequest,
    db: Session = Depends(get_db),
):
    logger.info("subscription.cycle called: %s", request.model_dump())

    existing = (
        db.query(DBPeriodicPayment)
        .filter(DBPeriodicPayment.stripe_payment_id == request.transaction_id)
        .first()
    )
    if existing and existing.sync_status == "success":
        logger.info(
            "subscription.cycle idempotent skip: transaction_id=%s",
            request.transaction_id,
        )
        _write_audit_log(
            db,
            "subscription.cycle",
            "cycle",
            str(request.team_id),
            200,
            {
                "transaction_id": request.transaction_id,
                "outcome": "idempotent_skip",
            },
        )
        return SubscriptionCycleResponse(
            status="ok",
            team_id=request.team_id,
            payment_id=existing.id,
            budget_dollars=request.budget_cents / 100.0,
            idempotent=True,
        )

    team = db.query(DBTeam).filter(DBTeam.id == request.team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail=f"Team {request.team_id} not found")
    if team.budget_type not in (BudgetType.PERIODIC, BudgetType.POOL):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Team {request.team_id} budget_type={team.budget_type} "
                "does not support subscription cycles"
            ),
        )

    region = db.query(DBRegion).filter(DBRegion.id == request.region_id).first()
    if not region:
        raise HTTPException(
            status_code=404, detail=f"Region {request.region_id} not found"
        )

    period_start = datetime.now(UTC)
    # Safety-net: Stripe cycles are 30d. The 31d budget_duration on LiteLLM
    # auto-expires budget if a webhook is missed. On cancellation, Stripe sends
    # customer.subscription.deleted which handles explicit cleanup.
    period_end = period_start + timedelta(days=31)

    is_first_cycle = (
        not db.query(DBPeriodicBudgetLedgerEntry)
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == team.id,
            DBPeriodicBudgetLedgerEntry.region_id == region.id,
            DBPeriodicBudgetLedgerEntry.entry_type == "subscription",
        )
        .first()
    )

    try:
        if not is_first_cycle:
            await capture_periodic_team_spend_for_period(
                db=db,
                team=team,
                region=region,
                period_start=period_start,
                period_end=period_end,
                source_event_id=request.transaction_id,
            )

        payment_id = await _record_periodic_payment_direct(
            db,
            team_id=team.id,
            transaction_id=request.transaction_id,
            amount_cents=request.budget_cents,
            currency="usd",
            payment_type="subscription",
        )

        await _sync_periodic_ledger_for_period(
            db=db,
            team=team,
            region=region,
            period_start=period_start,
            period_end=period_end,
            amount_cents=request.budget_cents,
            source_payment_id=payment_id,
            source_invoice_id=request.transaction_id,
        )

        sync_errors = await apply_billing_cycle_for_team(
            db=db,
            team_id=team.id,
            budget_cents=request.budget_cents,
            region_id=request.region_id,
            period_start=period_start,
            period_end=period_end,
            source_payment_id=payment_id,
        )

        # A LiteLLM sync failure is collected into sync_errors rather than
        # raised (see apply_billing_cycle_for_team), and the payment is stamped
        # sync_failed. If we returned 200 here the caller (MOAD's Stripe webhook)
        # would treat the cycle as applied and never retry, silently leaving the
        # team's budget un-provisioned until the next monthly cycle (issue
        # #617B). Return a non-2xx so MOAD retries; the payment stays in a
        # resubmittable sync_failed state (the idempotency guard only
        # short-circuits on sync_status == "success"), so a re-driven cycle
        # re-applies the budget.
        if sync_errors:
            _write_audit_log(
                db,
                "subscription.cycle",
                "cycle",
                str(team.id),
                502,
                {
                    "transaction_id": request.transaction_id,
                    "budget_cents": request.budget_cents,
                    "region_id": request.region_id,
                    "is_first_cycle": is_first_cycle,
                    "sync_errors": sync_errors,
                    "outcome": "sync_failed",
                },
            )
            logger.error(
                "subscription.cycle sync failed: team_id=%s transaction_id=%s sync_error_count=%s",
                team.id,
                request.transaction_id,
                len(sync_errors) if hasattr(sync_errors, "__len__") else 1,
            )
            raise HTTPException(
                status_code=502,
                detail="LiteLLM sync failed during subscription cycle; retry",
            )

        _write_audit_log(
            db,
            "subscription.cycle",
            "cycle",
            str(team.id),
            200,
            {
                "transaction_id": request.transaction_id,
                "budget_cents": request.budget_cents,
                "region_id": request.region_id,
                "is_first_cycle": is_first_cycle,
                "sync_errors": sync_errors,
                "outcome": "success",
            },
        )
        logger.info(
            "subscription.cycle success: team_id=%s transaction_id=%s",
            team.id,
            request.transaction_id,
        )
        return SubscriptionCycleResponse(
            status="ok",
            team_id=team.id,
            payment_id=payment_id,
            budget_dollars=request.budget_cents / 100.0,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "subscription.cycle failed: team_id=%s error=%s",
            request.team_id,
            exc,
            exc_info=True,
        )
        _write_audit_log(
            db,
            "subscription.cycle",
            "cycle",
            str(request.team_id),
            500,
            {
                "transaction_id": request.transaction_id,
                "error": str(exc),
                "outcome": "error",
            },
        )
        raise HTTPException(status_code=500, detail=f"Subscription cycle failed: {exc}")


@router.post(
    "/deactivate",
    response_model=SubscriptionDeactivateResponse,
    dependencies=[Depends(get_role_min_system_admin)],
)
async def subscription_deactivate(
    request: SubscriptionDeactivateRequest,
    db: Session = Depends(get_db),
):
    logger.info("subscription.deactivate called: %s", request.model_dump())

    existing = (
        db.query(DBPeriodicPayment)
        .filter(DBPeriodicPayment.stripe_payment_id == request.transaction_id)
        .first()
    )
    if existing and existing.sync_status == "success":
        logger.info(
            "subscription.deactivate idempotent skip: transaction_id=%s",
            request.transaction_id,
        )
        _write_audit_log(
            db,
            "subscription.deactivate",
            "deactivate",
            str(request.team_id),
            200,
            {
                "transaction_id": request.transaction_id,
                "outcome": "idempotent_skip",
            },
        )
        return SubscriptionDeactivateResponse(
            status="ok",
            team_id=request.team_id,
            payment_id=existing.id,
            idempotent=True,
        )

    team = db.query(DBTeam).filter(DBTeam.id == request.team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail=f"Team {request.team_id} not found")

    region = db.query(DBRegion).filter(DBRegion.id == request.region_id).first()
    if not region:
        raise HTTPException(
            status_code=404, detail=f"Region {request.region_id} not found"
        )

    try:
        active_subscription_period = (
            db.query(DBPeriodicBudgetLedgerEntry)
            .filter(
                DBPeriodicBudgetLedgerEntry.team_id == team.id,
                DBPeriodicBudgetLedgerEntry.region_id == region.id,
                DBPeriodicBudgetLedgerEntry.entry_type == "subscription",
                DBPeriodicBudgetLedgerEntry.is_active.is_(True),
                DBPeriodicBudgetLedgerEntry.effective_period_start.isnot(None),
                DBPeriodicBudgetLedgerEntry.effective_period_end.isnot(None),
            )
            .order_by(
                DBPeriodicBudgetLedgerEntry.effective_period_end.desc(),
                DBPeriodicBudgetLedgerEntry.id.desc(),
            )
            .first()
        )
        litellm_service = LiteLLMService(
            api_url=region.litellm_api_url,
            api_key=region.litellm_api_key,
        )
        lite_team_id = LiteLLMService.format_team_id(region.name, team.id)
        current_team_spend: float | None = None
        if active_subscription_period:
            await capture_periodic_team_spend_for_period(
                db=db,
                team=team,
                region=region,
                period_start=active_subscription_period.effective_period_start,
                period_end=active_subscription_period.effective_period_end,
                source_event_id=request.transaction_id,
            )
            # Debit mid-period spend against top-up entries so that
            # compute_active_topup_remaining reflects actual remaining balance.
            # Without this, FIFO never runs on the cancel path (no invoice),
            # and consumed_cents stays stale — leaking top-up credits.
            try:
                team_info_resp = await litellm_service.get_team_info(lite_team_id)
                team_info = team_info_resp.get("team_info", team_info_resp)
                current_team_spend = float(team_info.get("spend", 0.0) or 0.0)
                current_spend_cents = int(round(current_team_spend * 100))
                spend_baseline_cents = _previous_period_spend_baseline_cents(
                    db,
                    team_id=team.id,
                    region_id=region.id,
                    current_period_start=active_subscription_period.effective_period_start,
                )
                incremental_spend_cents = max(
                    0, current_spend_cents - spend_baseline_cents
                )
                if incremental_spend_cents > 0:
                    allocate_period_spend_fifo(
                        db,
                        team_id=team.id,
                        region_id=region.id,
                        spend_cents=incremental_spend_cents,
                    )
            except Exception as exc:
                db.rollback()
                logger.warning(
                    "Failed to run FIFO allocation on cancellation for team %s: %s",
                    team.id,
                    exc,
                    exc_info=True,
                )

        # Deactivation immediately ends active subscription windows.
        active_sub_rows = (
            db.query(DBPeriodicBudgetLedgerEntry)
            .filter(
                DBPeriodicBudgetLedgerEntry.team_id == team.id,
                DBPeriodicBudgetLedgerEntry.region_id == region.id,
                DBPeriodicBudgetLedgerEntry.entry_type == "subscription",
                DBPeriodicBudgetLedgerEntry.is_active.is_(True),
            )
            .all()
        )
        for row in active_sub_rows:
            row.is_active = False
        db.flush()

        topup_remaining_dollars = (
            compute_active_topup_remaining(db, team_id=team.id, region_id=region.id)
            / 100.0
        )
        if team.budget_type == BudgetType.PERIODIC:
            topup_budget_duration = f"{settings.PERIODIC_TOPUP_EXPIRY_DAYS}d"
        else:
            topup_budget_duration = f"{settings.POOL_PURCHASE_EXPIRY_DAYS}d"

        projected_team_max_budget = topup_remaining_dollars
        if current_team_spend is None:
            try:
                team_info_resp = await litellm_service.get_team_info(lite_team_id)
                team_info = team_info_resp.get("team_info", team_info_resp)
                current_team_spend = float(team_info.get("spend", 0.0) or 0.0)
            except Exception as exc:
                logger.warning(
                    "Failed to read LiteLLM team spend for deactivation projection (team %s): %s",
                    team.id,
                    exc,
                )
        if current_team_spend is not None:
            # LiteLLM team spend is non-resettable. Keep current spend as baseline
            # so remaining headroom stays requestable after deactivation.
            projected_team_max_budget = current_team_spend + topup_remaining_dollars

        try:
            await litellm_service.update_team_budget(
                team_id=lite_team_id,
                max_budget=projected_team_max_budget,
                budget_duration=topup_budget_duration,
                spend=0.0,
            )
        except Exception as exc:
            logger.error(
                "Failed to update LiteLLM deactivation budget for team %s: %s",
                team.id,
                exc,
            )

        keys = get_team_region_litellm_keys(db, team_id=team.id, region_id=region.id)
        for key in keys:
            try:
                key_cap = (
                    db.query(DBSpendCap.max_budget)
                    .filter(
                        DBSpendCap.scope == "key",
                        DBSpendCap.region_id == region.id,
                        DBSpendCap.key_id == key.id,
                        DBSpendCap.max_budget.isnot(None),
                    )
                    .first()
                )
                has_key_cap = key_cap is not None and key_cap[0] is not None
                if has_key_cap:
                    await litellm_service.set_key_restrictions(
                        litellm_token=key.litellm_token,
                        duration="31d",
                        budget_duration="31d",
                        budget_amount=float(key_cap[0]),
                        rpm_limit=None,
                        spend=0.0,
                    )
                else:
                    await litellm_service.set_key_restrictions(
                        litellm_token=key.litellm_token,
                        duration=topup_budget_duration,
                        budget_duration=topup_budget_duration,
                        budget_amount=topup_remaining_dollars,
                        rpm_limit=None,
                        spend=0.0,
                    )
            except Exception as exc:
                logger.error(
                    "Failed to update LiteLLM deactivation key budget for key %s: %s",
                    key.id,
                    exc,
                )

        payment_id = await _record_periodic_payment_direct(
            db,
            team_id=team.id,
            transaction_id=request.transaction_id,
            amount_cents=0,
            currency="usd",
            payment_type="deactivation",
        )

        _write_audit_log(
            db,
            "subscription.deactivate",
            "deactivate",
            str(team.id),
            200,
            {
                "transaction_id": request.transaction_id,
                "region_id": request.region_id,
                "reason": request.reason,
                "outcome": "success",
            },
        )
        logger.info(
            "subscription.deactivate success: team_id=%s transaction_id=%s",
            team.id,
            request.transaction_id,
        )
        return SubscriptionDeactivateResponse(
            status="ok",
            team_id=team.id,
            payment_id=payment_id,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "subscription.deactivate failed: team_id=%s error=%s",
            request.team_id,
            exc,
            exc_info=True,
        )
        _write_audit_log(
            db,
            "subscription.deactivate",
            "deactivate",
            str(request.team_id),
            500,
            {
                "transaction_id": request.transaction_id,
                "error": str(exc),
                "outcome": "error",
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Subscription deactivation failed: {exc}",
        )
