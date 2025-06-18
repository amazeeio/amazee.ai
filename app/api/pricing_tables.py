from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime, UTC

from app.db.database import get_db
from app.db.models import DBSystemSecret, DBTeam
from app.core.security import check_system_admin, get_role_min_team_admin, get_current_user_from_auth
from app.schemas.models import PricingTableCreate, PricingTableResponse, PricingTablesResponse

# Constants for pricing table keys
STANDARD_PRICING_TABLE_KEY = "CurrentPricingTable"
ALWAYS_FREE_PRICING_TABLE_KEY = "AlwaysFreePricingTable"

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
    Create or update a pricing table. Only accessible by system admin users.
    Can create/update either the standard pricing table or the always-free pricing table.
    """
    # Determine which table to update based on the table_type
    table_key = STANDARD_PRICING_TABLE_KEY if pricing_table.table_type == "standard" else ALWAYS_FREE_PRICING_TABLE_KEY
    table_description = "Current Stripe pricing table ID" if pricing_table.table_type == "standard" else "Always-free pricing table ID"

    # Check if the table already exists
    existing_table = db.query(DBSystemSecret).filter(DBSystemSecret.key == table_key).first()

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
            key=table_key,
            value=pricing_table.pricing_table_id,
            description=table_description,
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
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user_from_auth)
):
    """
    Get the current pricing table ID. Only accessible by team admin users or higher privileges.
    For always-free teams, returns the always-free pricing table.
    """
    # Load the team from the database
    team = db.query(DBTeam).filter(DBTeam.id == current_user.team_id).first()
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found"
        )

    # Check if the team is always-free
    if team.is_always_free:
        pricing_table = db.query(DBSystemSecret).filter(DBSystemSecret.key == ALWAYS_FREE_PRICING_TABLE_KEY).first()
    else:
        # For non-always-free teams, return the standard pricing table
        pricing_table = db.query(DBSystemSecret).filter(DBSystemSecret.key == STANDARD_PRICING_TABLE_KEY).first()
    if not pricing_table:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pricing table ID not found"
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
    pricing_table = db.query(DBSystemSecret).filter(DBSystemSecret.key == STANDARD_PRICING_TABLE_KEY).first()
    if not pricing_table:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pricing table found"
        )

    db.delete(pricing_table)
    db.commit()

    return {"message": "Pricing table deleted successfully"}

@router.get("/list", response_model=PricingTablesResponse, dependencies=[Depends(check_system_admin)])
async def get_all_pricing_tables(
    db: Session = Depends(get_db)
):
    """
    Get all pricing tables. Only accessible by system admin users.
    Returns both the standard and always-free pricing tables.
    """
    standard_table = db.query(DBSystemSecret).filter(DBSystemSecret.key == STANDARD_PRICING_TABLE_KEY).first()
    always_free_table = db.query(DBSystemSecret).filter(DBSystemSecret.key == ALWAYS_FREE_PRICING_TABLE_KEY).first()

    return PricingTablesResponse(
        standard=PricingTableResponse(
            pricing_table_id=standard_table.value if standard_table else None,
            updated_at=standard_table.updated_at or standard_table.created_at if standard_table else None
        ) if standard_table else None,
        always_free=PricingTableResponse(
            pricing_table_id=always_free_table.value if always_free_table else None,
            updated_at=always_free_table.updated_at or always_free_table.created_at if always_free_table else None
        ) if always_free_table else None
    )