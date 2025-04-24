from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from fastapi import status
from sqlalchemy.exc import IntegrityError
import requests
from pydantic import BaseModel
import logging

from app.db.database import get_db
from app.schemas.models import PrivateAIKey, PrivateAIKeyCreate, TeamPrivateAIKeyCreate, User
from app.db.postgres import PostgresManager
from app.db.models import DBPrivateAIKey, DBRegion, DBUser, DBTeam
from app.services.litellm import LiteLLMService
from app.core.security import get_current_user_from_auth, get_role_min_key_creator

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Private AI Keys"]
)

class BudgetPeriodUpdate(BaseModel):
    budget_duration: str

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

@router.delete("/{key_name}")
async def delete_private_ai_key(
    key_name: str,
    current_user = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    # Get the private AI key record
    private_ai_key = db.query(DBPrivateAIKey).filter(
        DBPrivateAIKey.database_name == key_name,
        DBPrivateAIKey.owner_id == current_user.id
    ).first()

    if not private_ai_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Private AI Key not found"
        )

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

@router.get("/{key_name}/spend")
async def get_private_ai_key_spend(
    key_name: str,
    current_user = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    # Get the private AI key record
    private_ai_key = db.query(DBPrivateAIKey).filter(
        DBPrivateAIKey.database_name == key_name
    ).first()

    if not private_ai_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Private AI Key not found"
        )

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
        # Get spend information from LiteLLM API
        response = requests.get(
            f"{region.litellm_api_url}/key/info",
            headers={
                "Authorization": f"Bearer {region.litellm_api_key}"
            },
            params={
                "key": private_ai_key.litellm_token
            }
        )
        response.raise_for_status()
        data = response.json()
        info = data.get("info", {})
        return {
            "spend": info.get("spend", 0),
            "expires": info.get("expires"),
            "created_at": info.get("created_at"),
            "updated_at": info.get("updated_at"),
            "max_budget": info.get("max_budget"),
            "budget_duration": info.get("budget_duration"),
            "budget_reset_at": info.get("budget_reset_at")
        }
    except requests.exceptions.RequestException as e:
        error_msg = str(e)
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_details = e.response.json()
                error_msg = f"Status {e.response.status_code}: {error_details}"
            except ValueError:
                error_msg = f"Status {e.response.status_code}: {e.response.text}"
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get spend information: {error_msg}"
        )

@router.put("/{key_name}/budget-period")
async def update_budget_period(
    key_name: str,
    budget_update: BudgetPeriodUpdate,
    current_user = Depends(get_current_user_from_auth),
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
    # Get the private AI key record
    private_ai_key = db.query(DBPrivateAIKey).filter(
        DBPrivateAIKey.database_name == key_name
    ).first()

    if not private_ai_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Private AI Key not found"
        )

    # Verify user has access to the key
    if not current_user.is_admin and private_ai_key.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to update this key"
        )

    # Get the region
    region = db.query(DBRegion).filter(DBRegion.id == private_ai_key.region_id).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Region not found"
        )

    try:
        # Update budget period in LiteLLM
        response = requests.post(
            f"{region.litellm_api_url}/key/update",
            headers={
                "Authorization": f"Bearer {region.litellm_api_key}"
            },
            json={
                "key": private_ai_key.litellm_token,
                "budget_duration": budget_update.budget_duration
            }
        )
        response.raise_for_status()

        # Get updated spend information
        spend_response = requests.get(
            f"{region.litellm_api_url}/key/info",
            headers={
                "Authorization": f"Bearer {region.litellm_api_key}"
            },
            params={
                "key": private_ai_key.litellm_token
            }
        )
        spend_response.raise_for_status()
        data = spend_response.json()
        info = data.get("info", {})

        return {
            "spend": info.get("spend", 0),
            "expires": info.get("expires"),
            "created_at": info.get("created_at"),
            "updated_at": info.get("updated_at"),
            "max_budget": info.get("max_budget"),
            "budget_duration": info.get("budget_duration"),
            "budget_reset_at": info.get("budget_reset_at")
        }
    except requests.exceptions.RequestException as e:
        error_msg = str(e)
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_details = e.response.json()
                error_msg = f"Status {e.response.status_code}: {error_details}"
            except ValueError:
                error_msg = f"Status {e.response.status_code}: {e.response.text}"
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update budget period: {error_msg}"
        )
