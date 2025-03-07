from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from fastapi import status
from sqlalchemy.exc import IntegrityError

from app.db.database import get_db
from app.api.auth import get_current_user_from_auth
from app.schemas.models import PrivateAIKey, PrivateAIKeyCreate, User
from app.db.postgres import PostgresManager
from app.db.models import DBPrivateAIKey, DBRegion, DBUser

router = APIRouter(
    tags=["Private AI Keys"]
)

@router.post("", response_model=PrivateAIKey)
@router.post("/", response_model=PrivateAIKey)
async def create_private_ai_key(
    private_ai_key: PrivateAIKeyCreate,
    current_user = Depends(get_current_user_from_auth),
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

    The response will include:
    - Database connection details (host, database name, username, password)
    - LiteLLM API token for authentication
    - LiteLLM API URL for making requests

    Note: You must be authenticated to use this endpoint.
    Only admins can create keys for other users.
    """
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

    # Determine the owner of the key
    owner_id = private_ai_key.owner_id if private_ai_key.owner_id is not None else current_user.id
    
    # If trying to create for another user, verify admin status
    if owner_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can create keys for other users"
        )

    # Get the owner user
    owner = db.query(DBUser).filter(DBUser.id == owner_id).first()
    if not owner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Owner user not found"
        )

    try:
        # Create new postgres database
        postgres_manager = PostgresManager(region=region)
        key_credentials = await postgres_manager.create_database(
            owner=owner.email,
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
            region_id=region.id
        )
        db.add(new_key)
        db.commit()
        db.refresh(new_key)

        # Add owner_id and region to the response
        key_credentials["owner_id"] = owner_id
        key_credentials["region"] = region.name
        key_credentials["litellm_api_url"] = region.litellm_api_url
        key_credentials["name"] = private_ai_key.name

        # Return credentials to user
        return key_credentials
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create Private AI Key: {str(e)}"
        )

@router.get("", response_model=List[PrivateAIKey])
@router.get("/", response_model=List[PrivateAIKey])
async def list_private_ai_keys(
    owner_id: Optional[int] = None,
    current_user = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    """
    List private AI keys.
    If user is admin:
        - Returns all keys if no owner_id is provided
        - Returns keys for specific owner if owner_id is provided
    If user is not admin:
        - Returns only their own keys, ignoring owner_id parameter
    """
    query = db.query(DBPrivateAIKey)

    if current_user.is_admin:
        if owner_id is not None:
            query = query.filter(DBPrivateAIKey.owner_id == owner_id)
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
    await postgres_manager.delete_database(
        key_name,
        litellm_token=private_ai_key.litellm_token
    )

    # Remove the private AI key record from the application database
    db.delete(private_ai_key)
    db.commit()

    return {"message": "Private AI Key deleted successfully"}