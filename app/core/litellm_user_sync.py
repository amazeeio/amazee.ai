import asyncio
import logging
import os
from time import perf_counter

from prometheus_client import Counter, Histogram
from app.db.models import DBRegion, DBTeam, DBUser
from app.services.litellm import LiteLLMService
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
SYNC_OPERATION_TIMEOUT_SECONDS = float(
    os.getenv("LITELLM_SYNC_OPERATION_TIMEOUT_SECONDS", "15")
)

litellm_user_sync_operations_total = Counter(
    "litellm_user_sync_operations_total",
    "Total LiteLLM sync operations by operation, region and outcome.",
    ["operation", "region", "outcome"],
)

litellm_user_sync_operation_duration_seconds = Histogram(
    "litellm_user_sync_operation_duration_seconds",
    "Latency for LiteLLM sync operations by operation, region and outcome.",
    ["operation", "region", "outcome"],
)

litellm_user_sync_skips_total = Counter(
    "litellm_user_sync_skips_total",
    "Total skipped LiteLLM sync batches.",
    ["sync_action", "reason"],
)


def _region_label(region: DBRegion) -> str:
    return region.name or f"region_{region.id}"


def _log_sync_event(level: str, message: str, **fields) -> None:
    log_fn = logger.info if level == "info" else logger.error
    payload = " ".join(f"{key}={value}" for key, value in fields.items())
    log_fn("%s %s", message, payload)


async def _run_sync_operation(
    *,
    operation: str,
    region: DBRegion,
    user_id: int | None,
    team_id: int | None,
    action,
) -> None:
    region_name = _region_label(region)
    started = perf_counter()
    try:
        await asyncio.wait_for(action(), timeout=SYNC_OPERATION_TIMEOUT_SECONDS)
        duration = perf_counter() - started
        litellm_user_sync_operations_total.labels(
            operation=operation, region=region_name, outcome="success"
        ).inc()
        litellm_user_sync_operation_duration_seconds.labels(
            operation=operation, region=region_name, outcome="success"
        ).observe(duration)
        _log_sync_event(
            "info",
            "litellm_sync_success",
            operation=operation,
            region_id=region.id,
            region=region_name,
            user_id=user_id,
            team_id=team_id,
            duration_ms=round(duration * 1000, 2),
        )
    except Exception as exc:
        duration = perf_counter() - started
        litellm_user_sync_operations_total.labels(
            operation=operation, region=region_name, outcome="failure"
        ).inc()
        litellm_user_sync_operation_duration_seconds.labels(
            operation=operation, region=region_name, outcome="failure"
        ).observe(duration)
        _log_sync_event(
            "error",
            "litellm_sync_failure",
            operation=operation,
            region_id=region.id,
            region=region_name,
            user_id=user_id,
            team_id=team_id,
            duration_ms=round(duration * 1000, 2),
            error=type(exc).__name__,
            error_message=str(exc),
        )
        raise


def _should_skip_litellm_sync(db_user: DBUser) -> bool:
    email = (db_user.email or "").lower()
    return email.startswith("trial-") and email.endswith("@example.com")


def team_role_for_litellm(db_user: DBUser) -> str:
    # LiteLLM OSS rejects team admin assignment via API (enterprise-only feature).
    # Keep API writes stable by mapping all team roles to LiteLLM "user".
    return "user"


def get_team_region(db: Session, team_id: int) -> DBRegion | None:
    """Return the single active region for the given team, or None."""
    team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not team or not team.region_id:
        return None
    return (
        db.query(DBRegion)
        .filter(DBRegion.id == team.region_id, DBRegion.is_active.is_(True))
        .first()
    )


async def sync_create_user_across_regions(
    db: Session,
    db_user: DBUser,
    team_id: int | None = None,
) -> None:
    if _should_skip_litellm_sync(db_user):
        litellm_user_sync_skips_total.labels(
            sync_action="sync_create_user_across_regions", reason="trial_user"
        ).inc()
        _log_sync_event(
            "info",
            "litellm_sync_skipped",
            sync_action="sync_create_user_across_regions",
            reason="trial_user",
            user_id=db_user.id,
            email=db_user.email,
            team_id=team_id,
        )
        return

    resolved_team_id = team_id if team_id is not None else db_user.team_id
    if resolved_team_id is None:
        _log_sync_event(
            "info",
            "litellm_sync_skipped",
            sync_action="sync_create_user_across_regions",
            reason="no_team",
            user_id=db_user.id,
        )
        return

    region = get_team_region(db, resolved_team_id)
    if not region:
        _log_sync_event(
            "info",
            "litellm_sync_skipped",
            sync_action="sync_create_user_across_regions",
            reason="no_active_region",
            user_id=db_user.id,
            team_id=resolved_team_id,
        )
        return

    service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    await _run_sync_operation(
        operation="create_user",
        region=region,
        user_id=db_user.id,
        team_id=resolved_team_id,
        action=lambda: service.create_user(
            user_id=str(db_user.id),
            user_email=db_user.email,
            auto_create_key=False,
        ),
    )
    lite_team_id = LiteLLMService.format_team_id(region.name, resolved_team_id)
    await _run_sync_operation(
        operation="add_team_member",
        region=region,
        user_id=db_user.id,
        team_id=resolved_team_id,
        action=lambda: service.add_team_member(
            team_id=lite_team_id,
            user_id=str(db_user.id),
            role=team_role_for_litellm(db_user),
        ),
    )


async def sync_add_user_to_team(
    db: Session,
    db_user: DBUser,
    team_id: int,
) -> None:
    if _should_skip_litellm_sync(db_user):
        litellm_user_sync_skips_total.labels(
            sync_action="sync_add_user_to_team", reason="trial_user"
        ).inc()
        _log_sync_event(
            "info",
            "litellm_sync_skipped",
            sync_action="sync_add_user_to_team",
            reason="trial_user",
            user_id=db_user.id,
            email=db_user.email,
            team_id=team_id,
        )
        return

    region = get_team_region(db, team_id)
    if not region:
        _log_sync_event(
            "info",
            "litellm_sync_skipped",
            sync_action="sync_add_user_to_team",
            reason="no_active_region",
            user_id=db_user.id,
            team_id=team_id,
        )
        return

    service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    await _run_sync_operation(
        operation="create_user",
        region=region,
        user_id=db_user.id,
        team_id=team_id,
        action=lambda: service.create_user(
            user_id=str(db_user.id),
            user_email=db_user.email,
            auto_create_key=False,
        ),
    )
    lite_team_id = LiteLLMService.format_team_id(region.name, team_id)
    await _run_sync_operation(
        operation="add_team_member",
        region=region,
        user_id=db_user.id,
        team_id=team_id,
        action=lambda: service.add_team_member(
            team_id=lite_team_id,
            user_id=str(db_user.id),
            role=team_role_for_litellm(db_user),
        ),
    )


async def sync_remove_user_from_team(
    db: Session,
    db_user: DBUser,
    team_id: int,
    region: DBRegion | None = None,
) -> None:
    """Remove a user from a team in LiteLLM.

    ``region`` can be supplied explicitly when the team's ``region_id`` has
    already been cleared (e.g. during a disassociation rollback).  When omitted
    the team's current region is resolved automatically.
    """
    if _should_skip_litellm_sync(db_user):
        litellm_user_sync_skips_total.labels(
            sync_action="sync_remove_user_from_team", reason="trial_user"
        ).inc()
        _log_sync_event(
            "info",
            "litellm_sync_skipped",
            sync_action="sync_remove_user_from_team",
            reason="trial_user",
            user_id=db_user.id,
            email=db_user.email,
            team_id=team_id,
        )
        return

    resolved_region = region or get_team_region(db, team_id)
    if not resolved_region:
        _log_sync_event(
            "info",
            "litellm_sync_skipped",
            sync_action="sync_remove_user_from_team",
            reason="no_active_region",
            user_id=db_user.id,
            team_id=team_id,
        )
        return

    service = LiteLLMService(
        api_url=resolved_region.litellm_api_url, api_key=resolved_region.litellm_api_key
    )
    lite_team_id = LiteLLMService.format_team_id(resolved_region.name, team_id)
    await _run_sync_operation(
        operation="remove_team_member",
        region=resolved_region,
        user_id=db_user.id,
        team_id=team_id,
        action=lambda: service.remove_team_member(
            team_id=lite_team_id, user_id=str(db_user.id)
        ),
    )


async def sync_update_user_team_role(
    db: Session,
    db_user: DBUser,
    team_id: int,
) -> None:
    if _should_skip_litellm_sync(db_user):
        litellm_user_sync_skips_total.labels(
            sync_action="sync_update_user_team_role", reason="trial_user"
        ).inc()
        _log_sync_event(
            "info",
            "litellm_sync_skipped",
            sync_action="sync_update_user_team_role",
            reason="trial_user",
            user_id=db_user.id,
            email=db_user.email,
            team_id=team_id,
        )
        return

    role = team_role_for_litellm(db_user)
    if role == "user":
        # Current OSS role mapping is constant. Skip the call.
        litellm_user_sync_skips_total.labels(
            sync_action="sync_update_user_team_role", reason="no_role_change"
        ).inc()
        _log_sync_event(
            "info",
            "litellm_sync_skipped",
            sync_action="sync_update_user_team_role",
            reason="no_role_change",
            user_id=db_user.id,
            team_id=team_id,
        )
        return

    region = get_team_region(db, team_id)
    if not region:
        _log_sync_event(
            "info",
            "litellm_sync_skipped",
            sync_action="sync_update_user_team_role",
            reason="no_active_region",
            user_id=db_user.id,
            team_id=team_id,
        )
        return

    service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    lite_team_id = LiteLLMService.format_team_id(region.name, team_id)
    await _run_sync_operation(
        operation="update_team_member",
        region=region,
        user_id=db_user.id,
        team_id=team_id,
        action=lambda: service.update_team_member(
            team_id=lite_team_id, user_id=str(db_user.id), role=role
        ),
    )


async def sync_delete_user_across_regions(
    db: Session,
    db_user: DBUser,
    team_id: int | None = None,
) -> None:
    if _should_skip_litellm_sync(db_user):
        litellm_user_sync_skips_total.labels(
            sync_action="sync_delete_user_across_regions", reason="trial_user"
        ).inc()
        _log_sync_event(
            "info",
            "litellm_sync_skipped",
            sync_action="sync_delete_user_across_regions",
            reason="trial_user",
            user_id=db_user.id,
            email=db_user.email,
            team_id=team_id,
        )
        return

    resolved_team_id = team_id if team_id is not None else db_user.team_id
    if resolved_team_id is None:
        _log_sync_event(
            "info",
            "litellm_sync_skipped",
            sync_action="sync_delete_user_across_regions",
            reason="no_team",
            user_id=db_user.id,
        )
        return

    region = get_team_region(db, resolved_team_id)
    if not region:
        _log_sync_event(
            "info",
            "litellm_sync_skipped",
            sync_action="sync_delete_user_across_regions",
            reason="no_active_region",
            user_id=db_user.id,
            team_id=resolved_team_id,
        )
        return

    service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    lite_team_id = LiteLLMService.format_team_id(region.name, resolved_team_id)
    try:
        await _run_sync_operation(
            operation="remove_team_member",
            region=region,
            user_id=db_user.id,
            team_id=resolved_team_id,
            action=lambda: service.remove_team_member(
                team_id=lite_team_id, user_id=str(db_user.id)
            ),
        )
    except Exception:
        _log_sync_event(
            "warning",
            "remove_team_member_before_delete_failed",
            region_id=region.id,
            region=region.name,
            user_id=db_user.id,
            team_id=resolved_team_id,
            error_message="Proceeding with user deletion despite team removal failure",
        )
    await _run_sync_operation(
        operation="delete_user",
        region=region,
        user_id=db_user.id,
        team_id=resolved_team_id,
        action=lambda: service.delete_user(user_id=str(db_user.id)),
    )
