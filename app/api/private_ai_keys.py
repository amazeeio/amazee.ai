from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from fastapi import status
from sqlalchemy.exc import IntegrityError

from app.db.database import get_db
from app.api.auth import get_current_user_from_auth
from app.schemas.models import PrivateAIKey, PrivateAIKeyCreate, User
from app.db.postgres import PostgresManager
from app.db.models import DBPrivateAIKey, DBRegion

router = APIRouter()

@router.post("", response_model=PrivateAIKey)
@router.post("/", response_model=PrivateAIKey)
async def create_private_ai_key(
    private_ai_key: PrivateAIKeyCreate,
    current_user = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
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

    try:
        # Create new postgres database
        postgres_manager = PostgresManager(region=region)
        key_credentials = await postgres_manager.create_database(
            owner=current_user.email,
            user_id=current_user.id
        )

        # Store private AI key info in main application database
        new_key = DBPrivateAIKey(
            database_name=key_credentials["database_name"],
            host=key_credentials["host"],
            username=key_credentials["username"],
            password=key_credentials["password"],
            litellm_token=key_credentials["litellm_token"],
            litellm_api_url=region.litellm_api_url,
            owner_id=current_user.id,
            region_id=region.id
        )
        db.add(new_key)
        db.commit()
        db.refresh(new_key)

        # Add owner_id and region to the response
        key_credentials["owner_id"] = current_user.id
        key_credentials["region"] = region.name
        key_credentials["litellm_api_url"] = region.litellm_api_url

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
    current_user = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    private_ai_keys = db.query(DBPrivateAIKey).filter(DBPrivateAIKey.owner_id == current_user.id).all()
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