from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime, UTC
from typing import Optional

from app.db.database import get_db
from app.db.models import DBTeam, DBPricingTable
from app.core.security import get_role_min_system_admin, get_role_min_team_admin, get_current_user_from_auth
from app.schemas.models import PricingTableCreate, PricingTableResponse, PricingTablesResponse
from app.core.config import settings

# Constants for pricing table types
VALID_TABLE_TYPES = ["standard", "always_free", "gpt"]

router = APIRouter(
    tags=["pricing-tables"]
)

@router.post("", response_model=PricingTableResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(get_role_min_system_admin)])
@router.post("/", response_model=PricingTableResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(get_role_min_system_admin)])
async def create_pricing_table(
    pricing_table: PricingTableCreate,
    db: Session = Depends(get_db)
):
    """
    Create or update a pricing table. Only accessible by system admin users.
    Can create/update pricing tables of type: standard, always_free, or gpt.
    """
    # Use provided stripe_publishable_key or fall back to system config
    stripe_publishable_key = pricing_table.stripe_publishable_key or settings.STRIPE_PUBLISHABLE_KEY

    # Check if the table already exists
    existing_table = db.query(DBPricingTable).filter(
        DBPricingTable.table_type == pricing_table.table_type,
        DBPricingTable.is_active.is_(True)
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
    table_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user_from_auth)
):
    """
    Get the pricing table ID. Only accessible by team admin users or higher privileges.
    If table_type is not provided, defaults to "standard" unless the team is marked as "always_free".
    Valid table types: standard, always_free, gpt
    """
    # Determine table type if not provided
    if table_type is None:
        # Load the team from the database
        team = db.query(DBTeam).filter(DBTeam.id == current_user.team_id).first()
        if not team:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Team not found"
            )
        table_type = "always_free" if team.is_always_free else "standard"
    else:
        # Validate table type if provided
        if table_type not in VALID_TABLE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid table type. Must be one of: {', '.join(VALID_TABLE_TYPES)}"
            )

    # Get the pricing table
    pricing_table = db.query(DBPricingTable).filter(
        DBPricingTable.table_type == table_type,
        DBPricingTable.is_active.is_(True)
    ).first()

    if not pricing_table:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pricing table of type '{table_type}' not found"
        )

    return PricingTableResponse(
        pricing_table_id=pricing_table.pricing_table_id,
        stripe_publishable_key=pricing_table.stripe_publishable_key,
        updated_at=pricing_table.updated_at or pricing_table.created_at
    )

@router.delete("", dependencies=[Depends(get_role_min_system_admin)])
@router.delete("/", dependencies=[Depends(get_role_min_system_admin)])
async def delete_pricing_table(
    table_type: str,
    db: Session = Depends(get_db)
):
    """
    Delete a pricing table by type. Only accessible by system admin users.
    Valid table types: standard, always_free, gpt
    """
    # Validate table type
    if table_type not in VALID_TABLE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid table type. Must be one of: {', '.join(VALID_TABLE_TYPES)}"
        )

    pricing_table = db.query(DBPricingTable).filter(
        DBPricingTable.table_type == table_type,
        DBPricingTable.is_active.is_(True)
    ).first()
    if not pricing_table:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No pricing table of type '{table_type}' found"
        )

    # Actually delete the record from the database
    db.delete(pricing_table)
    db.commit()

    return {"message": f"Pricing table of type '{table_type}' deleted successfully"}

@router.get("/list", response_model=PricingTablesResponse, dependencies=[Depends(get_role_min_system_admin)])
async def get_all_pricing_tables(
    db: Session = Depends(get_db)
):
    """
    Get all pricing tables. Only accessible by system admin users.
    Returns all active pricing tables by type.
    """
    # Get all active pricing tables
    pricing_tables = db.query(DBPricingTable).filter(
        DBPricingTable.is_active.is_(True)
    ).all()

    # Build response dictionary
    tables_dict = {}
    for table in pricing_tables:
        tables_dict[table.table_type] = PricingTableResponse(
            pricing_table_id=table.pricing_table_id,
            stripe_publishable_key=table.stripe_publishable_key,
            updated_at=table.updated_at or table.created_at
        )

    return PricingTablesResponse(tables=tables_dict)