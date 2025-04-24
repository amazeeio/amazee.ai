from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from fastapi import status
import logging

from app.db.database import get_db
from app.schemas.models import PrivateAIKey, PrivateAIKeyCreate, PrivateAIKeySpend, BudgetPeriodUpdate
from app.db.postgres import PostgresManager
from app.db.models import DBPrivateAIKey, DBRegion, DBUser, DBTeam
from app.services.litellm import LiteLLMService
from app.core.security import get_current_user_from_auth, get_role_min_key_creator, get_role_min_team_admin

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Private AI Keys"]
)

@router.post("", response_model=PrivateAIKey)
@router.post("/", response_model=PrivateAIKey)
async def create_private_ai_key(
    private_ai_key: PrivateAIKeyCreate,
    current_user = Depends(get_current_user_from_auth),
    user_role: str = Depends(get_role_min_key_creator),
    db: Session = Depends(get_db)
):
    """
    Create a new private AI key.

    This endpoint will:
    1. Create a new database in the specified region
    2. Set up necessary credentials and permissions
    3. Return connection details and tokens

    Required parameters:
    - **region_id**: The ID of the region where you want to create the key
    - **name**: The name for the private AI key

    Optional parameters:
    - **owner_id**: The ID of the user who will own this key (admin only)
    - **team_id**: The ID of the team that will own this key (admin only)

    The response will include:
    - Database connection details (host, database name, username, password)
    - LiteLLM API token for authentication
    - LiteLLM API URL for making requests

    Note: You must be authenticated to use this endpoint.
    Only admins can create keys for other users or teams.
    """
    owner_id = private_ai_key.owner_id
    team_id = private_ai_key.team_id
    # If no owner_id or team_id is specified, use the current user's ID
    if owner_id is None and team_id is None:
        owner_id = current_user.id

    # Fail fast without having to do DB lookups
    if user_role in ["key_creator", "user"]:
        if owner_id != current_user.id or team_id is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to perform this action"
            )
    elif user_role == "admin": # roles always refer to the team role, so admin is a team admin
        if team_id is not None and team_id != current_user.team_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to perform this action"
            )

    if team_id is not None and owner_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either owner_id or team_id must be specified, not both"
        )

    # Get the region
    region = db.query(DBRegion).filter(
        DBRegion.id == private_ai_key.region_id,
        DBRegion.is_active == True
    ).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Region not found or inactive"
        )

    # Get the owner user if different from current user
    owner = None
    if owner_id is not None and owner_id != current_user.id:
        owner = db.query(DBUser).filter(DBUser.id == owner_id).first()
        if not owner or (user_role == "admin" and owner.team_id != current_user.team_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Owner user not found"
            )
    else:
        owner = current_user

    # Get the team if team_id is specified
    team = None
    if team_id is not None:
        team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
        if not team:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Team not found"
            )

    if team is not None:
        owner_email = team.admin_email
    else:
        owner_email = owner.email

    try:
        # Create new postgres database
        postgres_manager = PostgresManager(region=region)

        # Generate LiteLLM token first
        litellm_service = LiteLLMService(
            api_url=region.litellm_api_url,
            api_key=region.litellm_api_key
        )
        litellm_token = await litellm_service.create_key(
            email=owner_email,
            name=private_ai_key.name,
            user_id=owner_id
        )

        # Create database with the generated token
        key_credentials = await postgres_manager.create_database(
            owner=owner_email,
            litellm_token=litellm_token,
            name=private_ai_key.name,
            user_id=owner_id
        )

        # Store private AI key info in main application database
        new_key = DBPrivateAIKey(
            database_name=key_credentials["database_name"],
            name=private_ai_key.name,
            database_host=key_credentials["database_host"],
            database_username=key_credentials["database_username"],
            database_password=key_credentials["database_password"],
            litellm_token=key_credentials["litellm_token"],
            litellm_api_url=region.litellm_api_url,
            owner_id=owner_id,
            team_id=team_id,
            region_id=region.id
        )
        db.add(new_key)
        db.commit()
        db.refresh(new_key)

        # Add metadata to the response
        key_credentials["owner_id"] = owner_id
        key_credentials["team_id"] = team_id
        key_credentials["region"] = region.name
        key_credentials["litellm_api_url"] = region.litellm_api_url
        key_credentials["name"] = private_ai_key.name

        return key_credentials
    except Exception as e:
        logger.error(f"Failed to create Private AI Key: {str(e)}", exc_info=True)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create Private AI Key: {str(e)}"
        )

@router.get("", response_model=List[PrivateAIKey])
@router.get("/", response_model=List[PrivateAIKey])
async def list_private_ai_keys(
    owner_id: Optional[int] = None,
    team_id: Optional[int] = None,
    current_user = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    """
    List private AI keys.
    If user is admin:
        - Returns all keys if no owner_id or team_id is provided
        - Returns keys for specific owner if owner_id is provided
        - Returns keys for specific team if team_id is provided
    If user is team admin:
        - Returns keys owned by users in their team AND keys owned by their team
    If user is not admin:
        - Returns only their own keys, ignoring owner_id and team_id parameters
    """
    query = db.query(DBPrivateAIKey)

    if current_user.is_admin:
        if owner_id is not None:
            query = query.filter(DBPrivateAIKey.owner_id == owner_id)
        elif team_id is not None:
            # Get all users in the team
            team_users = db.query(DBUser).filter(DBUser.team_id == team_id).all()
            team_user_ids = [user.id for user in team_users]
            # Return keys owned by users in the team OR owned by the team
            query = query.filter(
                (DBPrivateAIKey.owner_id.in_(team_user_ids)) |
                (DBPrivateAIKey.team_id == team_id)
            )
    else:
        # Check if user is a team admin
        if current_user.team_id is not None and current_user.role == "admin":
            # Get all users in the team
            team_users = db.query(DBUser).filter(DBUser.team_id == current_user.team_id).all()
            team_user_ids = [user.id for user in team_users]
            # Return keys owned by any user in the team OR owned by the team
            query = query.filter(
                (DBPrivateAIKey.owner_id.in_(team_user_ids)) |
                (DBPrivateAIKey.team_id == current_user.team_id)
            )
        else:
            # Non-admin users can only see their own keys
            query = query.filter(DBPrivateAIKey.owner_id == current_user.id)

    private_ai_keys = query.all()
    return [key.to_dict() for key in private_ai_keys]

def _get_key_if_allowed(key_name: str, current_user: DBUser, user_role: str, db: Session) -> DBPrivateAIKey:
    # First try to find the key
    private_ai_key = db.query(DBPrivateAIKey).filter(
        DBPrivateAIKey.database_name == key_name
    ).first()

    if not private_ai_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Private AI Key not found"
        )

    # Check if user has permission to view the key
    if current_user.is_admin:
        # System admin can view any key
        pass
    elif user_role == "admin":
        # Team admin can only view keys from their team
        if private_ai_key.team_id is not None:
            # For team-owned keys, check if it belongs to the admin's team
            if private_ai_key.team_id != current_user.team_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Private AI Key not found"
                )
        else:
            # For user-owned keys, check if the owner is in the admin's team
            owner = db.query(DBUser).filter(DBUser.id == private_ai_key.owner_id).first()
            if not owner or owner.team_id != current_user.team_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Private AI Key not found"
                )
    else:
        # Regular users can only view their own keys
        if private_ai_key.owner_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Private AI Key not found"
            )
    return private_ai_key

@router.delete("/{key_name}")
async def delete_private_ai_key(
    key_name: str,
    current_user = Depends(get_current_user_from_auth),
    user_role: str = Depends(get_role_min_key_creator),
    db: Session = Depends(get_db)
):
    private_ai_key = _get_key_if_allowed(key_name, current_user, user_role, db)
    # Get the region
    region = db.query(DBRegion).filter(DBRegion.id == private_ai_key.region_id).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Region not found"
        )

    # Delete database and remove from user's list
    postgres_manager = PostgresManager(region=region)

    # Delete LiteLLM token first
    litellm_service = LiteLLMService(
        api_url=region.litellm_api_url,
        api_key=region.litellm_api_key
    )
    await litellm_service.delete_key(private_ai_key.litellm_token)

    # Delete the database
    await postgres_manager.delete_database(
        private_ai_key.database_name
    )

    # Remove the private AI key record from the application database
    db.delete(private_ai_key)
    db.commit()

    return {"message": "Private AI Key deleted successfully"}

@router.get("/{key_name}/spend", response_model=PrivateAIKeySpend)
async def get_private_ai_key_spend(
    key_name: str,
    current_user = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    user_role = current_user.role
    private_ai_key = _get_key_if_allowed(key_name, current_user, user_role, db)

    # Get the region
    region = db.query(DBRegion).filter(DBRegion.id == private_ai_key.region_id).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Region not found"
        )

    # Create LiteLLM service instance
    litellm_service = LiteLLMService(
        api_url=region.litellm_api_url,
        api_key=region.litellm_api_key
    )

    try:
        data = await litellm_service.get_key_info(private_ai_key.litellm_token)
        info = data.get("info", {})

        # Only set default for spend field
        spend_info = {
            "spend": info.get("spend", 0.0),
            **info
        }

        return PrivateAIKeySpend.model_validate(spend_info)
    except Exception as e:
        logger.error(f"Failed to get Private AI Key spend: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get Private AI Key spend: {str(e)}"
        )

@router.put("/{key_name}/budget-period")
async def update_budget_period(
    key_name: str,
    budget_update: BudgetPeriodUpdate,
    current_user = Depends(get_current_user_from_auth),
    user_role: str = Depends(get_role_min_team_admin),
    db: Session = Depends(get_db)
):
    """
    Update the budget period for a private AI key.

    This endpoint will:
    1. Verify the user has access to the key
    2. Update the budget period in LiteLLM
    3. Return the updated spend information

    Required parameters:
    - **budget_duration**: The new budget period (e.g. "monthly", "weekly", "daily")

    Note: You must be authenticated to use this endpoint.
    Only the owner of the key or an admin can update it.
    """
    private_ai_key = _get_key_if_allowed(key_name, current_user, user_role, db)

    # Get the region
    region = db.query(DBRegion).filter(DBRegion.id == private_ai_key.region_id).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Region not found"
        )

    litellm_service = LiteLLMService(
        api_url=region.litellm_api_url,
        api_key=region.litellm_api_key
    )

    try:
        # Update budget period in LiteLLM
        await litellm_service.update_budget(
            litellm_token=private_ai_key.litellm_token,
            budget_duration=budget_update.budget_duration
        )

        # Get updated spend information
        spend_data = await litellm_service.get_key_info(private_ai_key.litellm_token)
        # Only set default for spend field
        spend_info = {
            "spend": spend_data.get("spend", 0.0),
            **spend_data
        }

        return PrivateAIKeySpend.model_validate(spend_info)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update budget period: {str(e)}"
        )
