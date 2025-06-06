from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime, UTC

from app.db.database import get_db
from app.db.models import DBSystemSecret
from app.core.security import check_system_admin, get_role_min_team_admin
from app.schemas.models import PricingTableCreate, PricingTableResponse

router = APIRouter(
    tags=["pricing-tables"]
)

@router.post("", response_model=PricingTableResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(check_system_admin)])
@router.post("/", response_model=PricingTableResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(check_system_admin)])
async def create_pricing_table(
    pricing_table: PricingTableCreate,
    db: Session = Depends(get_db)
):
    """
    Create or update the current pricing table. Only accessible by system admin users.
    There can only be one active pricing table at a time.
    """
    # Check if a pricing table already exists
    existing_table = db.query(DBSystemSecret).filter(DBSystemSecret.key == "CurrentPricingTable").first()

    if existing_table:
        # Update existing table
        existing_table.value = pricing_table.pricing_table_id
        existing_table.updated_at = datetime.now(UTC)
        db.commit()
        db.refresh(existing_table)
        return PricingTableResponse(
            pricing_table_id=existing_table.value,
            updated_at=existing_table.updated_at
        )
    else:
        # Create new table
        db_table = DBSystemSecret(
            key="CurrentPricingTable",
            value=pricing_table.pricing_table_id,
            description="Current Stripe pricing table ID",
            created_at=datetime.now(UTC)
        )
        db.add(db_table)
        db.commit()
        db.refresh(db_table)
        return PricingTableResponse(
            pricing_table_id=db_table.value,
            updated_at=db_table.created_at
        )

@router.get("", response_model=PricingTableResponse, dependencies=[Depends(get_role_min_team_admin)])
@router.get("/", response_model=PricingTableResponse, dependencies=[Depends(get_role_min_team_admin)])
async def get_pricing_table(
    db: Session = Depends(get_db)
):
    """
    Get the current pricing table ID. Only accessible by team admin users or higher privileges.
    """
    pricing_table = db.query(DBSystemSecret).filter(DBSystemSecret.key == "CurrentPricingTable").first()
    if not pricing_table:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pricing table found"
        )
    return PricingTableResponse(
        pricing_table_id=pricing_table.value,
        updated_at=pricing_table.updated_at or pricing_table.created_at
    )

@router.delete("", dependencies=[Depends(check_system_admin)])
@router.delete("/", dependencies=[Depends(check_system_admin)])
async def delete_pricing_table(
    db: Session = Depends(get_db)
):
    """
    Delete the current pricing table. Only accessible by system admin users.
    """
    pricing_table = db.query(DBSystemSecret).filter(DBSystemSecret.key == "CurrentPricingTable").first()
    if not pricing_table:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pricing table found"
        )

    db.delete(pricing_table)
    db.commit()

    return {"message": "Pricing table deleted successfully"}