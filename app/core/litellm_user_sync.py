import logging
import os
import asyncio
from time import perf_counter
from typing import Iterable, List

from prometheus_client import Counter, Histogram
from app.db.models import DBRegion, DBTeamRegion, DBUser
from app.services.litellm import LiteLLMService
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
SYNC_MAX_CONCURRENCY = int(os.getenv("LITELLM_SYNC_MAX_CONCURRENCY", "4"))
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


async def _run_per_region_with_bounded_concurrency(
    regions: list[DBRegion], region_runner
) -> None:
    semaphore = asyncio.Semaphore(max(1, SYNC_MAX_CONCURRENCY))

    async def _wrapped(region: DBRegion) -> None:
        async with semaphore:
            await region_runner(region)

    await asyncio.gather(*(_wrapped(region) for region in regions))


def _should_skip_litellm_sync(db_user: DBUser) -> bool:
    email = (db_user.email or "").lower()
    return email.startswith("trial-") and email.endswith("@example.com")


def _dedupe_regions(regions: Iterable[DBRegion]) -> List[DBRegion]:
    seen: set[int] = set()
    deduped: List[DBRegion] = []
    for region in regions:
        if region.id in seen:
            continue
        seen.add(region.id)
        deduped.append(region)
    return deduped


def team_role_for_litellm(db_user: DBUser) -> str:
    # LiteLLM OSS rejects team admin assignment via API (enterprise-only feature).
    # Keep API writes stable by mapping all team roles to LiteLLM "user".
    return "user"


def get_target_regions_for_user(db: Session, team_id: int | None) -> List[DBRegion]:
    shared_regions = (
        db.query(DBRegion)
        .filter(DBRegion.is_active.is_(True), DBRegion.is_dedicated.is_(False))
        .all()
    )
    if team_id is None:
        return shared_regions

    dedicated_regions = (
        db.query(DBRegion)
        .join(DBTeamRegion, DBTeamRegion.region_id == DBRegion.id)
        .filter(
            DBRegion.is_active.is_(True),
            DBRegion.is_dedicated.is_(True),
            DBTeamRegion.team_id == team_id,
        )
        .all()
    )
    return _dedupe_regions([*shared_regions, *dedicated_regions])


async def sync_create_user_across_regions(
    db: Session,
    db_user: DBUser,
    team_id: int | None = None,
    force_regions: List[DBRegion] | None = None,
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

    regions = force_regions or get_target_regions_for_user(db, team_id)
    _log_sync_event(
        "info",
        "litellm_sync_batch_start",
        sync_action="sync_create_user_across_regions",
        user_id=db_user.id,
        team_id=team_id,
        region_count=len(regions),
    )

    async def _region_runner(region: DBRegion) -> None:
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
        if team_id is not None:
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

    await _run_per_region_with_bounded_concurrency(regions, _region_runner)
    _log_sync_event(
        "info",
        "litellm_sync_batch_complete",
        sync_action="sync_create_user_across_regions",
        user_id=db_user.id,
        team_id=team_id,
        region_count=len(regions),
    )


async def sync_add_user_to_team(
    db: Session,
    db_user: DBUser,
    team_id: int,
    force_regions: List[DBRegion] | None = None,
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

    regions = force_regions or get_target_regions_for_user(db, team_id)
    _log_sync_event(
        "info",
        "litellm_sync_batch_start",
        sync_action="sync_add_user_to_team",
        user_id=db_user.id,
        team_id=team_id,
        region_count=len(regions),
    )

    async def _region_runner(region: DBRegion) -> None:
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

    await _run_per_region_with_bounded_concurrency(regions, _region_runner)
    _log_sync_event(
        "info",
        "litellm_sync_batch_complete",
        sync_action="sync_add_user_to_team",
        user_id=db_user.id,
        team_id=team_id,
        region_count=len(regions),
    )


async def sync_remove_user_from_team(
    db: Session,
    db_user: DBUser,
    team_id: int,
    force_regions: List[DBRegion] | None = None,
) -> None:
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

    regions = force_regions or get_target_regions_for_user(db, team_id)
    _log_sync_event(
        "info",
        "litellm_sync_batch_start",
        sync_action="sync_remove_user_from_team",
        user_id=db_user.id,
        team_id=team_id,
        region_count=len(regions),
    )

    async def _region_runner(region: DBRegion) -> None:
        service = LiteLLMService(
            api_url=region.litellm_api_url, api_key=region.litellm_api_key
        )
        lite_team_id = LiteLLMService.format_team_id(region.name, team_id)
        await _run_sync_operation(
            operation="remove_team_member",
            region=region,
            user_id=db_user.id,
            team_id=team_id,
            action=lambda: service.remove_team_member(
                team_id=lite_team_id, user_id=str(db_user.id)
            ),
        )

    await _run_per_region_with_bounded_concurrency(regions, _region_runner)
    _log_sync_event(
        "info",
        "litellm_sync_batch_complete",
        sync_action="sync_remove_user_from_team",
        user_id=db_user.id,
        team_id=team_id,
        region_count=len(regions),
    )


async def sync_update_user_team_role(
    db: Session,
    db_user: DBUser,
    team_id: int,
    force_regions: List[DBRegion] | None = None,
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
        # Current OSS role mapping is constant. Skip cross-region calls.
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
    regions = force_regions or get_target_regions_for_user(db, team_id)
    _log_sync_event(
        "info",
        "litellm_sync_batch_start",
        sync_action="sync_update_user_team_role",
        user_id=db_user.id,
        team_id=team_id,
        region_count=len(regions),
    )

    async def _region_runner(region: DBRegion) -> None:
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

    await _run_per_region_with_bounded_concurrency(regions, _region_runner)
    _log_sync_event(
        "info",
        "litellm_sync_batch_complete",
        sync_action="sync_update_user_team_role",
        user_id=db_user.id,
        team_id=team_id,
        region_count=len(regions),
    )


async def sync_delete_user_across_regions(
    db: Session,
    db_user: DBUser,
    team_id: int | None = None,
    force_regions: List[DBRegion] | None = None,
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

    regions = force_regions or get_target_regions_for_user(db, team_id)
    _log_sync_event(
        "info",
        "litellm_sync_batch_start",
        sync_action="sync_delete_user_across_regions",
        user_id=db_user.id,
        team_id=team_id,
        region_count=len(regions),
    )

    async def _region_runner(region: DBRegion) -> None:
        service = LiteLLMService(
            api_url=region.litellm_api_url, api_key=region.litellm_api_key
        )
        if team_id is not None:
            lite_team_id = LiteLLMService.format_team_id(region.name, team_id)
            await _run_sync_operation(
                operation="remove_team_member",
                region=region,
                user_id=db_user.id,
                team_id=team_id,
                action=lambda: service.remove_team_member(
                    team_id=lite_team_id, user_id=str(db_user.id)
                ),
            )
        await _run_sync_operation(
            operation="delete_user",
            region=region,
            user_id=db_user.id,
            team_id=team_id,
            action=lambda: service.delete_user(user_id=str(db_user.id)),
        )

    await _run_per_region_with_bounded_concurrency(regions, _region_runner)
    _log_sync_event(
        "info",
        "litellm_sync_batch_complete",
        sync_action="sync_delete_user_across_regions",
        user_id=db_user.id,
        team_id=team_id,
        region_count=len(regions),
    )
