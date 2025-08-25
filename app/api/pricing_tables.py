from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime, UTC

from app.db.database import get_db
from app.db.models import DBSystemSecret, DBTeam, DBPricingTable
from app.core.security import check_system_admin, get_role_min_team_admin, get_current_user_from_auth
from app.schemas.models import PricingTableCreate, PricingTableResponse, PricingTablesResponse
from app.core.config import settings

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
    # Use provided stripe_publishable_key or fall back to system config
    stripe_publishable_key = pricing_table.stripe_publishable_key or settings.STRIPE_PUBLISHABLE_KEY

    # Check if the table already exists
    existing_table = db.query(DBPricingTable).filter(
        DBPricingTable.table_type == pricing_table.table_type,
        DBPricingTable.is_active == True
    ).first()

    if existing_table:
        # Update existing table
        existing_table.pricing_table_id = pricing_table.pricing_table_id
        existing_table.stripe_publishable_key = stripe_publishable_key
        existing_table.updated_at = datetime.now(UTC)
        db.commit()
        db.refresh(existing_table)
        return PricingTableResponse(
            pricing_table_id=existing_table.pricing_table_id,
            stripe_publishable_key=existing_table.stripe_publishable_key,
            updated_at=existing_table.updated_at
        )
    else:
        # Create new table
        db_table = DBPricingTable(
            table_type=pricing_table.table_type,
            pricing_table_id=pricing_table.pricing_table_id,
            stripe_publishable_key=stripe_publishable_key,
            is_active=True,
            created_at=datetime.now(UTC)
        )
        db.add(db_table)
        db.commit()
        db.refresh(db_table)
        return PricingTableResponse(
            pricing_table_id=db_table.pricing_table_id,
            stripe_publishable_key=db_table.stripe_publishable_key,
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
    table_type = "always_free" if team.is_always_free else "standard"

    # Try new DBPricingTable first
    pricing_table = db.query(DBPricingTable).filter(
        DBPricingTable.table_type == table_type,
        DBPricingTable.is_active == True
    ).first()

    if pricing_table:
        # Use new table format
        return PricingTableResponse(
            pricing_table_id=pricing_table.pricing_table_id,
            stripe_publishable_key=pricing_table.stripe_publishable_key,
            updated_at=pricing_table.updated_at or pricing_table.created_at
        )

    # Fallback to old DBSystemSecret method
    table_key = ALWAYS_FREE_PRICING_TABLE_KEY if team.is_always_free else STANDARD_PRICING_TABLE_KEY
    old_pricing_table = db.query(DBSystemSecret).filter(DBSystemSecret.key == table_key).first()

    if not old_pricing_table:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pricing table ID not found"
        )

    # Return with system default stripe_publishable_key for old format
    return PricingTableResponse(
        pricing_table_id=old_pricing_table.value,
        stripe_publishable_key=settings.STRIPE_PUBLISHABLE_KEY,
        updated_at=old_pricing_table.updated_at or old_pricing_table.created_at
    )

@router.delete("", dependencies=[Depends(check_system_admin)])
@router.delete("/", dependencies=[Depends(check_system_admin)])
async def delete_pricing_table(
    db: Session = Depends(get_db)
):
    """
    Delete the current pricing table. Only accessible by system admin users.
    """
    pricing_table = db.query(DBPricingTable).filter(
        DBPricingTable.table_type == "standard",
        DBPricingTable.is_active == True
    ).first()
    if not pricing_table:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pricing table found"
        )

    # Soft delete by setting is_active to False
    pricing_table.is_active = False
    pricing_table.updated_at = datetime.now(UTC)
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
    # Try new DBPricingTable first
    standard_table = db.query(DBPricingTable).filter(
        DBPricingTable.table_type == "standard",
        DBPricingTable.is_active == True
    ).first()
    always_free_table = db.query(DBPricingTable).filter(
        DBPricingTable.table_type == "always_free",
        DBPricingTable.is_active == True
    ).first()

    # Fallback to old DBSystemSecret method
    old_standard_table = db.query(DBSystemSecret).filter(DBSystemSecret.key == STANDARD_PRICING_TABLE_KEY).first()
    old_always_free_table = db.query(DBSystemSecret).filter(DBSystemSecret.key == ALWAYS_FREE_PRICING_TABLE_KEY).first()

    # Build response using new tables when available, fallback to old when not
    standard_response = None
    if standard_table:
        standard_response = PricingTableResponse(
            pricing_table_id=standard_table.pricing_table_id,
            stripe_publishable_key=standard_table.stripe_publishable_key,
            updated_at=standard_table.updated_at or standard_table.created_at
        )
    elif old_standard_table:
        standard_response = PricingTableResponse(
            pricing_table_id=old_standard_table.value,
            stripe_publishable_key=settings.STRIPE_PUBLISHABLE_KEY,
            updated_at=old_standard_table.updated_at or old_standard_table.created_at
        )

    always_free_response = None
    if always_free_table:
        always_free_response = PricingTableResponse(
            pricing_table_id=always_free_table.pricing_table_id,
            stripe_publishable_key=always_free_table.stripe_publishable_key,
            updated_at=always_free_table.updated_at or always_free_table.created_at
        )
    elif old_always_free_table:
        always_free_response = PricingTableResponse(
            pricing_table_id=old_always_free_table.value,
            stripe_publishable_key=settings.STRIPE_PUBLISHABLE_KEY,
            updated_at=old_always_free_table.updated_at or old_always_free_table.created_at
        )

    return PricingTablesResponse(
        standard=standard_response,
        always_free=always_free_response
    )