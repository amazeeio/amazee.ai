from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime, UTC

from app.db.database import get_db
from app.db.models import DBProduct
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
    # Check if stripe_lookup_key already exists
    existing_product = db.query(DBProduct).filter(DBProduct.stripe_lookup_key == product.stripe_lookup_key).first()
    if existing_product:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Product with this stripe_lookup_key already exists"
        )

    # Create the product
    db_product = DBProduct(
        name=product.name,
        stripe_lookup_key=product.stripe_lookup_key,
        active=product.active,
        created_at=datetime.now(UTC)
    )

    db.add(db_product)
    db.commit()
    db.refresh(db_product)

    return db_product

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
    product_id: int,
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
    product_id: int,
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

    # If updating stripe_lookup_key, check if it already exists
    if product_update.stripe_lookup_key and product_update.stripe_lookup_key != product.stripe_lookup_key:
        existing_product = db.query(DBProduct).filter(
            DBProduct.stripe_lookup_key == product_update.stripe_lookup_key,
            DBProduct.id != product_id
        ).first()
        if existing_product:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Product with this stripe_lookup_key already exists"
            )

    # Update the product
    for key, value in product_update.model_dump(exclude_unset=True).items():
        setattr(product, key, value)

    db.commit()
    db.refresh(product)

    return product

@router.delete("/{product_id}", dependencies=[Depends(check_system_admin)])
async def delete_product(
    product_id: int,
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

    db.delete(product)
    db.commit()

    return {"message": "Product deleted successfully"}