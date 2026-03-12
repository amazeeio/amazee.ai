from datetime import UTC, datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import (
    get_current_user_from_auth,
    get_role_min_system_admin,
    get_role_min_team_admin,
)
from app.db.database import get_db
from app.db.models import DBPoolTopupProduct, DBRegion
from app.schemas.models import (
    PoolTopupProduct,
    PoolTopupProductCreate,
    PoolTopupProductUpdate,
)

router = APIRouter(tags=["pool-topups"])


@router.post(
    "",
    response_model=PoolTopupProduct,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_role_min_system_admin)],
)
@router.post(
    "/",
    response_model=PoolTopupProduct,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_role_min_system_admin)],
)
async def create_pool_topup_product(
    payload: PoolTopupProductCreate, db: Session = Depends(get_db)
):
    existing = (
        db.query(DBPoolTopupProduct)
        .filter(DBPoolTopupProduct.stripe_price_id == payload.stripe_price_id)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Top-up with this Stripe price already exists",
        )

    if payload.region_id is not None:
        region = (
            db.query(DBRegion)
            .filter(DBRegion.id == payload.region_id, DBRegion.is_active.is_(True))
            .first()
        )
        if not region:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Region not found",
            )

    topup = DBPoolTopupProduct(
        name=payload.name,
        stripe_price_id=payload.stripe_price_id,
        stripe_product_id=payload.stripe_product_id,
        amount_cents=payload.amount_cents,
        currency=payload.currency.lower(),
        region_id=payload.region_id,
        is_active=payload.is_active,
        created_at=datetime.now(UTC),
    )
    db.add(topup)
    db.commit()
    db.refresh(topup)
    return topup


@router.get(
    "",
    response_model=List[PoolTopupProduct],
    dependencies=[Depends(get_role_min_team_admin)],
)
@router.get(
    "/",
    response_model=List[PoolTopupProduct],
    dependencies=[Depends(get_role_min_team_admin)],
)
async def list_pool_topup_products(
    region_id: int | None = None,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user_from_auth),
):
    query = db.query(DBPoolTopupProduct)

    if region_id is not None:
        query = query.filter(
            (DBPoolTopupProduct.region_id == region_id)
            | (DBPoolTopupProduct.region_id.is_(None))
        )

    if not current_user.is_admin or not include_inactive:
        query = query.filter(DBPoolTopupProduct.is_active.is_(True))

    return query.order_by(DBPoolTopupProduct.amount_cents.asc()).all()


@router.put(
    "/{topup_id}",
    response_model=PoolTopupProduct,
    dependencies=[Depends(get_role_min_system_admin)],
)
async def update_pool_topup_product(
    topup_id: int, payload: PoolTopupProductUpdate, db: Session = Depends(get_db)
):
    topup = db.query(DBPoolTopupProduct).filter(DBPoolTopupProduct.id == topup_id).first()
    if not topup:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Top-up not found")

    update_data = payload.model_dump(exclude_unset=True)
    if "currency" in update_data and update_data["currency"]:
        update_data["currency"] = update_data["currency"].lower()

    if "region_id" in update_data and update_data["region_id"] is not None:
        region = (
            db.query(DBRegion)
            .filter(DBRegion.id == update_data["region_id"], DBRegion.is_active.is_(True))
            .first()
        )
        if not region:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Region not found",
            )

    if "stripe_price_id" in update_data and update_data["stripe_price_id"]:
        existing = (
            db.query(DBPoolTopupProduct)
            .filter(
                DBPoolTopupProduct.stripe_price_id == update_data["stripe_price_id"],
                DBPoolTopupProduct.id != topup_id,
            )
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Top-up with this Stripe price already exists",
            )

    for key, value in update_data.items():
        setattr(topup, key, value)

    topup.updated_at = datetime.now(UTC)
    db.add(topup)
    db.commit()
    db.refresh(topup)
    return topup


@router.delete("/{topup_id}", dependencies=[Depends(get_role_min_system_admin)])
async def delete_pool_topup_product(topup_id: int, db: Session = Depends(get_db)):
    topup = db.query(DBPoolTopupProduct).filter(DBPoolTopupProduct.id == topup_id).first()
    if not topup:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Top-up not found")

    topup.is_active = False
    topup.updated_at = datetime.now(UTC)
    db.add(topup)
    db.commit()

    return {"message": "Top-up product deactivated"}
