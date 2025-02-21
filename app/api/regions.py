from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from app.db.database import get_db
from app.api.auth import get_current_user_from_auth
from app.schemas.models import Region, RegionCreate, RegionResponse, User, RegionUpdate
from app.db.models import DBRegion, DBPrivateAIKey

router = APIRouter()

@router.post("", response_model=Region)
@router.post("/", response_model=Region)
async def create_region(
    region: RegionCreate,
    current_user: User = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can create regions"
        )

    # Check if region with this name already exists
    existing_region = db.query(DBRegion).filter(DBRegion.name == region.name).first()
    if existing_region:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A region with the name '{region.name}' already exists"
        )

    db_region = DBRegion(**region.model_dump())
    db.add(db_region)
    try:
        db.commit()
        db.refresh(db_region)
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create region: {str(e)}"
        )
    return db_region

@router.get("", response_model=List[RegionResponse])
@router.get("/", response_model=List[RegionResponse])
async def list_regions(
    current_user: User = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    return db.query(DBRegion).filter(DBRegion.is_active == True).all()

@router.get("/admin", response_model=List[Region])
async def list_admin_regions(
    current_user: User = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can access this endpoint"
        )
    return db.query(DBRegion).all()

@router.get("/{region_id}", response_model=RegionResponse)
async def get_region(
    region_id: int,
    current_user: User = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Region not found"
        )
    return region

@router.delete("/{region_id}")
async def delete_region(
    region_id: int,
    current_user: User = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can delete regions"
        )

    region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Region not found"
        )

    # Check if there are any databases using this region
    existing_databases = db.query(DBPrivateAIKey).filter(DBPrivateAIKey.region_id == region_id).count()
    if existing_databases > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete region: {existing_databases} database(s) are currently using this region. Please delete these databases first."
        )

    # Instead of deleting, mark as inactive
    region.is_active = False
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to delete region: {str(e)}"
        )
    return {"message": "Region deleted successfully"}

@router.put("/{region_id}", response_model=Region)
async def update_region(
    region_id: int,
    region: RegionUpdate,
    current_user: User = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can update regions"
        )

    db_region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not db_region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Region not found"
        )

    # Check if updating to a name that already exists (excluding current region)
    if region.name != db_region.name:
        existing_region = db.query(DBRegion).filter(
            DBRegion.name == region.name,
            DBRegion.id != region_id
        ).first()
        if existing_region:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"A region with the name '{region.name}' already exists"
            )

    # Update the region fields
    update_data = region.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_region, field, value)

    try:
        db.commit()
        db.refresh(db_region)
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to update region: {str(e)}"
        )
    return db_region