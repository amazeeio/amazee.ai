from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime, UTC

from app.db.database import get_db
from app.db.models import DBProduct, DBTeamProduct
from app.core.security import check_system_admin, get_current_user_from_auth, get_role_min_team_admin
from app.schemas.models import Product, ProductCreate, ProductUpdate

router = APIRouter(
    tags=["products"]
)

@router.post("", response_model=Product, status_code=status.HTTP_201_CREATED, dependencies=[Depends(check_system_admin)])
@router.post("/", response_model=Product, status_code=status.HTTP_201_CREATED, dependencies=[Depends(check_system_admin)])
async def create_product(
    product: ProductCreate,
    db: Session = Depends(get_db)
):
    """
    Create a new product. Only accessible by system admin users.
    """
    # Check if product ID already exists
    existing_product = db.query(DBProduct).filter(DBProduct.id == product.id).first()
    if existing_product:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Product with this ID already exists"
        )

    # Create the product with all fields
    db_product = DBProduct(
        id=product.id,
        name=product.name,
        user_count=product.user_count,
        keys_per_user=product.keys_per_user,
        total_key_count=product.total_key_count,
        service_key_count=product.service_key_count,
        max_budget_per_key=product.max_budget_per_key,
        rpm_per_key=product.rpm_per_key,
        vector_db_count=product.vector_db_count,
        vector_db_storage=product.vector_db_storage,
        renewal_period_days=product.renewal_period_days,
        active=product.active,
        created_at=datetime.now(UTC)
    )

    db.add(db_product)
    db.commit()
    db.refresh(db_product)

    return db_product

@router.get("", response_model=List[Product], dependencies=[Depends(get_role_min_team_admin)])
@router.get("/", response_model=List[Product], dependencies=[Depends(get_role_min_team_admin)])
async def list_products(
    db: Session = Depends(get_db)
):
    """
    List all products. Only accessible by team admin users or higher privileges.
    """
    return db.query(DBProduct).all()

@router.get("/{product_id}", response_model=Product, dependencies=[Depends(get_role_min_team_admin)])
async def get_product(
    product_id: str,
    db: Session = Depends(get_db)
):
    """
    Get a specific product by ID. Only accessible by team admin users or higher privileges.
    """
    product = db.query(DBProduct).filter(DBProduct.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found"
        )
    return product

@router.put("/{product_id}", response_model=Product, dependencies=[Depends(check_system_admin)])
async def update_product(
    product_id: str,
    product_update: ProductUpdate,
    db: Session = Depends(get_db)
):
    """
    Update a product. Only accessible by system admin users.
    """
    product = db.query(DBProduct).filter(DBProduct.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found"
        )

    # Update the product with all provided fields
    update_data = product_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(product, key, value)

    product.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(product)

    return product

@router.delete("/{product_id}", dependencies=[Depends(check_system_admin)])
async def delete_product(
    product_id: str,
    db: Session = Depends(get_db)
):
    """
    Delete a product. Only accessible by system admin users.
    """
    product = db.query(DBProduct).filter(DBProduct.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found"
        )

    # Check if the product is associated with any teams
    team_association = db.query(DBTeamProduct).filter(DBTeamProduct.product_id == product_id).first()
    if team_association:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete product that is associated with one or more teams"
        )

    db.delete(product)
    db.commit()

    return {"message": "Product deleted successfully"}