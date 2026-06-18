"""
Internal API endpoints — machine-to-machine only, not for end users.

POST /internal/provision-key
    Called exclusively by moad during the external key provisioning flow.
    Authenticated via the existing admin API token mechanism (same as all
    other moad → amazee.ai calls).  moad passes AMAZEEAI_ADMIN_API_TOKEN
    which security.py resolves to an admin DBUser via the api_tokens table.
"""

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.dependencies import get_limit_service
from app.core.limit_service import LimitService
from app.core.security import get_current_user_from_auth, get_role_min_system_admin
from app.core.roles import UserRole
from app.db.database import get_db
from app.db.models import DBUser
from app.schemas.models import PrivateAIKey, PrivateAIKeyCreate

logger = logging.getLogger(__name__)

router = APIRouter(tags=["internal"])


@router.post(
    "/provision-key",
    response_model=PrivateAIKey,
    dependencies=[Depends(get_role_min_system_admin)],
)
async def internal_provision_key(
    private_ai_key: PrivateAIKeyCreate,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db),
    limit_service: LimitService = Depends(get_limit_service),
):
    """
    Create a private AI key (LiteLLM token + vector database) on behalf of moad.

    Called exclusively by moad as part of the external key provisioning flow
    (``POST /api/external/provision-key`` on the moad side).  The caller must
    present a valid amazee.ai admin API token via ``Authorization: Bearer <token>``.

    moad is expected to supply a valid ``team_id`` referencing the Applications
    workspace team it has already registered on this backend.
    """
    # Import here to avoid a circular import — internal.py and private_ai_keys.py
    # are sibling modules; deferring the import keeps the module graph clean.
    from app.api.private_ai_keys import create_private_ai_key

    # Internal provisioning always bypasses moad delegation — construct a
    # synthetic request that carries the bypass header.
    bypass_request = Request(
        scope={
            "type": "http",
            "method": "POST",
            "path": "/internal/provision-key",
            "headers": [(b"x-amazee-source", b"internal")],
            "query_string": b"",
        }
    )

    return await create_private_ai_key(
        request=bypass_request,
        private_ai_key=private_ai_key,
        current_user=current_user,
        user_role=UserRole.SYSTEM_ADMIN,
        db=db,
        limit_service=limit_service,
    )
