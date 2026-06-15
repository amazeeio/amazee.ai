from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session
import logging
import os
from datetime import UTC, datetime

from app.core.security import (
    get_role_min_specific_team_admin,
    get_role_min_system_admin,
)
from app.db.database import get_db
from app.db.models import DBProduct, DBTeam, DBTeamProduct
from app.schemas.models import (
    PortalRequest,
    PricingTableSession,
    SubscriptionCreate,
    SubscriptionResponse,
)
from app.services.stripe import (
    cancel_subscription,
    create_portal_session,
    create_stripe_customer,
    create_zero_rated_stripe_subscription,
    get_pricing_table_secret,
    get_subscribed_products_for_customer,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["billing"])


def get_return_url(team_id: int) -> str:
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    return f"{frontend_url}/teams/{team_id}/dashboard"


@router.post(
    "/teams/{team_id}/portal", dependencies=[Depends(get_role_min_specific_team_admin)]
)
async def get_portal(
    team_id: int,
    portal_request: PortalRequest = PortalRequest(),
    db: Session = Depends(get_db),
):
    team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Team not found"
        )
    if not team.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Team has not been registered with Stripe",
        )

    try:
        return_url = (
            portal_request.return_url
            if portal_request.return_url
            else get_return_url(team_id)
        )
        portal_url = await create_portal_session(team.stripe_customer_id, return_url)
        return Response(
            status_code=status.HTTP_303_SEE_OTHER, headers={"Location": portal_url}
        )
    except Exception as e:
        logger.error(f"Error creating portal session: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating portal session",
        )


@router.get(
    "/teams/{team_id}/pricing-table-session",
    dependencies=[Depends(get_role_min_specific_team_admin)],
    response_model=PricingTableSession,
)
async def get_pricing_table_session(team_id: int, db: Session = Depends(get_db)):
    team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Team not found"
        )

    try:
        if not team.stripe_customer_id:
            logger.info(f"Creating Stripe customer for team {team.id}")
            team.stripe_customer_id = await create_stripe_customer(team)
            db.add(team)
            db.commit()

        logger.info(f"Stripe ID is {team.stripe_customer_id}")
        client_secret = await get_pricing_table_secret(team.stripe_customer_id)
        return PricingTableSession(client_secret=client_secret)
    except Exception as e:
        logger.error(f"Error creating customer session: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating customer session",
        )


@router.post(
    "/teams/{team_id}/subscriptions",
    dependencies=[Depends(get_role_min_system_admin)],
    response_model=SubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_team_subscription(
    team_id: int, subscription_data: SubscriptionCreate, db: Session = Depends(get_db)
):
    team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Team not found"
        )

    product = (
        db.query(DBProduct).filter(DBProduct.id == subscription_data.product_id).first()
    )
    if not product:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product with ID {subscription_data.product_id} not found in database",
        )

    existing_subscription = (
        db.query(DBTeamProduct).filter(DBTeamProduct.team_id == team_id).first()
    )
    if existing_subscription:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Team {team_id} is already subscribed to a product",
        )

    try:
        if not team.stripe_customer_id:
            logger.info(f"Creating Stripe customer for team {team.id}")
            team.stripe_customer_id = await create_stripe_customer(team)
            db.add(team)
            db.commit()

        subscription_id = await create_zero_rated_stripe_subscription(
            customer_id=team.stripe_customer_id, product_id=subscription_data.product_id
        )
        logger.info(
            f"Created subscription {subscription_id} for team {team.id} to product {subscription_data.product_id}"
        )

        return SubscriptionResponse(
            subscription_id=subscription_id,
            product_id=subscription_data.product_id,
            team_id=team_id,
            created_at=datetime.now(UTC),
        )
    except Exception as e:
        logger.error(f"Error creating subscription for team {team_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating subscription: {str(e)}",
        )


@router.delete(
    "/teams/{team_id}/subscription/{product_id}",
    dependencies=[Depends(get_role_min_system_admin)],
)
async def delete_team_subscription(
    team_id: int, product_id: str, db: Session = Depends(get_db)
):
    team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Team not found"
        )

    product = db.query(DBProduct).filter(DBProduct.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product with ID {product_id} not found in database",
        )

    existing_subscription = (
        db.query(DBTeamProduct)
        .filter(
            DBTeamProduct.team_id == team_id, DBTeamProduct.product_id == product_id
        )
        .first()
    )
    if not existing_subscription or not team.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Team {team_id} is not associated with product {product_id}",
        )

    stripe_products = await get_subscribed_products_for_customer(
        team.stripe_customer_id
    )
    for stripe_subscription, stripe_product in stripe_products:
        if stripe_product == product_id:
            await cancel_subscription(stripe_subscription)
    db.delete(existing_subscription)
    db.commit()

    return {"message": "Successfully removed product"}
