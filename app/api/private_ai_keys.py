from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from fastapi import status
import logging

from app.db.database import get_db
from app.schemas.models import (
    PrivateAIKey, PrivateAIKeyCreate, PrivateAIKeySpend,
    BudgetPeriodUpdate, LiteLLMToken, VectorDBCreate, VectorDB
)
from app.db.postgres import PostgresManager
from app.db.models import DBPrivateAIKey, DBRegion, DBUser, DBTeam
from app.services.litellm import LiteLLMService
from app.core.security import get_current_user_from_auth, get_role_min_key_creator, get_role_min_team_admin, UserRole

# Set up logging
logger = logging.getLogger(__name__)

# Fake ID for resources not stored in the database
FAKE_ID = -1

def _validate_permissions_and_get_ownership_info(
    owner_id: Optional[int],
    team_id: Optional[int],
    current_user: DBUser,
    user_role: UserRole
) -> tuple[Optional[int], Optional[int]]:
    """
    Helper function to determine ownership information based on user role and input.
    Returns a tuple of (owner_id, team_id).
    """
    # If no owner_id or team_id is specified, use the current user's ID
    if owner_id is None and team_id is None:
        owner_id = current_user.id

    # Fail fast without having to do DB lookups
    personal_users : list[UserRole] = ["key_creator", "user"] # 'user' is the non-admin system user
    if user_role in personal_users:
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

    return owner_id, team_id

router = APIRouter(
    tags=["Private AI Keys"]
)

@router.post("/vector-db", response_model=VectorDB)
async def create_vector_db(
    vector_db: VectorDBCreate,
    current_user = Depends(get_current_user_from_auth),
    user_role: UserRole = Depends(get_role_min_key_creator),
    db: Session = Depends(get_db),
    store_result: bool = True
):
    """
    Create a new vector database.

    This endpoint will:
    1. Create a new database in the specified region
    2. Set up necessary credentials and permissions
    3. Return connection details

    Required parameters:
    - **region_id**: The ID of the region where you want to create the database
    - **name**: The name for the database

    Optional parameters:
    - **owner_id**: The ID of the user who will own this database (admin only)
    - **team_id**: The ID of the team that will own this database (admin only)

    The response will include:
    - Database connection details (host, database name, username, password)
    - Owner and team information
    - Region name

    Note: You must be authenticated to use this endpoint.
    Only admins can create databases for other users or teams.
    """
    # Get ownership information
    owner_id, team_id = _validate_permissions_and_get_ownership_info(
        vector_db.owner_id,
        vector_db.team_id,
        current_user,
        user_role
    )

    # Get the region
    region = db.query(DBRegion).filter(
        DBRegion.id == vector_db.region_id,
        DBRegion.is_active == True
    ).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Region not found or inactive"
        )

    try:
        # Create new postgres database
        postgres_manager = PostgresManager(region=region)

        # Create database
        key_credentials = await postgres_manager.create_database()

        # Create response object
        db_ai_key = DBPrivateAIKey(
            database_name=key_credentials["database_name"],
            database_host=key_credentials["database_host"],
            database_username=key_credentials["database_username"],
            database_password=key_credentials["database_password"],
            owner_id=owner_id,
            team_id=team_id,
            name=vector_db.name,
            region_id = vector_db.region_id
        )

        # If store_result is True, store the vector DB info in DBPrivateAIKey
        if store_result:
            db.add(db_ai_key)
            db.commit()
            db.refresh(db_ai_key)
        else:
            db_ai_key.region = region
            db_ai_key.id = FAKE_ID

        return VectorDB.model_validate(db_ai_key.to_dict())
    except Exception as e:
        logger.error(f"Failed to create vector database: {str(e)}", exc_info=True)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create vector database: {str(e)}"
        )

@router.post("", response_model=PrivateAIKey)
@router.post("/", response_model=PrivateAIKey)
async def create_private_ai_key(
    private_ai_key: PrivateAIKeyCreate,
    current_user = Depends(get_current_user_from_auth),
    user_role: UserRole = Depends(get_role_min_key_creator),
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
    # First create the LiteLLM token
    llm_token = await create_llm_token(private_ai_key, current_user, user_role, db, store_result=False)

    # Then create the vector database
    vector_db = VectorDBCreate(
        region_id=private_ai_key.region_id,
        name=private_ai_key.name,
        owner_id=private_ai_key.owner_id,
        team_id=private_ai_key.team_id
    )
    db_info = await create_vector_db(vector_db, current_user, user_role, db, store_result=False)

    # Store private AI key info in main application database
    new_key = DBPrivateAIKey(
        database_name=db_info.database_name,
        name=db_info.name,
        database_host=db_info.database_host,
        database_username=db_info.database_username,
        database_password=db_info.database_password,
        litellm_token=llm_token.litellm_token,
        litellm_api_url=llm_token.litellm_api_url,
        owner_id=db_info.owner_id,
        team_id=db_info.team_id,
        region_id=private_ai_key.region_id
    )
    db.add(new_key)
    db.commit()
    db.refresh(new_key)

    key_data = new_key.to_dict()

    return PrivateAIKey.model_validate(key_data)

@router.post("/token", response_model=LiteLLMToken)
async def create_llm_token(
    private_ai_key: PrivateAIKeyCreate,
    current_user = Depends(get_current_user_from_auth),
    user_role: UserRole = Depends(get_role_min_key_creator),
    db: Session = Depends(get_db),
    store_result: bool = True
):
    """
    Create a new LiteLLM token without creating a vector database.

    This endpoint will:
    1. Create a new LiteLLM token in the specified region
    2. Return the token and related information

    Required parameters:
    - **region_id**: The ID of the region where you want to create the token
    - **name**: The name for the token

    Optional parameters:
    - **owner_id**: The ID of the user who will own this token (admin only)
    - **team_id**: The ID of the team that will own this token (admin only)

    The response will include:
    - LiteLLM API token for authentication
    - LiteLLM API URL for making requests

    Note: You must be authenticated to use this endpoint.
    Only admins can create tokens for other users or teams.
    """
    # Get ownership information
    owner_id, team_id = _validate_permissions_and_get_ownership_info(
        private_ai_key.owner_id,
        private_ai_key.team_id,
        current_user,
        user_role
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
        litellm_team = team.id
    else:
        owner_email = owner.email
        litellm_team = owner.team_id or FAKE_ID

    try:
        # Generate LiteLLM token
        litellm_service = LiteLLMService(
            api_url=region.litellm_api_url,
            api_key=region.litellm_api_key
        )
        litellm_token = await litellm_service.create_key(
            email=owner_email,
            name=private_ai_key.name,
            user_id=owner_id,
            team_id=litellm_team
        )

        # Create response object
        db_token = DBPrivateAIKey(
            litellm_token=litellm_token,
            litellm_api_url=region.litellm_api_url,
            owner_id=owner_id,
            team_id=team_id,
            name=private_ai_key.name,
            region_id = private_ai_key.region_id
        )

        # If store_result is True, store the LiteLLM token info in DBPrivateAIKey
        if store_result:
            db.add(db_token)
            db.commit()
            db.refresh(db_token)
        else:
            db_token.region = region
            db_token.id = FAKE_ID

        return LiteLLMToken.model_validate(db_token.to_dict())
    except Exception as e:
        logger.error(f"Failed to create LiteLLM token: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create LiteLLM token: {str(e)}"
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
        - Returns their own keys, and keys for their team, ignoring owner_id and team_id parameters
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
            # Non-admin users can see their own keys and team-owned keys
            query = query.filter(
                (DBPrivateAIKey.owner_id == current_user.id) |
                (DBPrivateAIKey.team_id == current_user.team_id)
            )

    private_ai_keys = query.all()
    return [key.to_dict() for key in private_ai_keys]

def _get_key_if_allowed(key_id: int, current_user: DBUser, user_role: UserRole, db: Session) -> DBPrivateAIKey:
    # First try to find the key
    private_ai_key = db.query(DBPrivateAIKey).filter(
        DBPrivateAIKey.id == key_id
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

@router.delete("/{key_id}")
async def delete_private_ai_key(
    key_id: int,
    current_user = Depends(get_current_user_from_auth),
    user_role: UserRole = Depends(get_role_min_key_creator),
    db: Session = Depends(get_db)
):
    private_ai_key = _get_key_if_allowed(key_id, current_user, user_role, db)
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

@router.get("/{key_id}/spend", response_model=PrivateAIKeySpend)
async def get_private_ai_key_spend(
    key_id: int,
    current_user = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    user_role = current_user.role
    private_ai_key = _get_key_if_allowed(key_id, current_user, user_role, db)

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

@router.put("/{key_id}/budget-period")
async def update_budget_period(
    key_id: int,
    budget_update: BudgetPeriodUpdate,
    current_user = Depends(get_current_user_from_auth),
    user_role: UserRole = Depends(get_role_min_team_admin),
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
    private_ai_key = _get_key_if_allowed(key_id, current_user, user_role, db)

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
