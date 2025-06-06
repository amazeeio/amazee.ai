from datetime import datetime, UTC, timedelta
from sqlalchemy.orm import Session
from app.db.models import DBTeam, DBProduct, DBTeamProduct, DBPrivateAIKey, DBUser, DBRegion
from app.services.litellm import LiteLLMService
from app.services.ses import SESService
import logging
from collections import defaultdict
from app.core.resource_limits import get_token_restrictions
from app.services.stripe import (
    get_product_id_from_session,
    get_product_id_from_subscription,
    KNOWN_EVENTS,
    SUBSCRIPTION_SUCCESS_EVENTS,
    SESSION_FAILURE_EVENTS,
    SUBSCRIPTION_FAILURE_EVENTS,
    INVOICE_FAILURE_EVENTS,
    INVOICE_SUCCESS_EVENTS
)
from prometheus_client import Gauge, Counter
from typing import Dict, List
from app.core.security import create_access_token
from app.core.config import settings
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

# Prometheus metrics
team_freshness_days = Gauge(
    "team_freshness_days",
    "Age of teams in days (since creation for teams without products, since last payment for teams with products)",
    ["team_id", "team_name"]
)

team_expired_metric = Counter(
    "team_expired_total",
    "Total number of teams that have expired without products",
    ["team_id", "team_name"]
)

key_spend_percentage = Gauge(
    "key_spend_percentage",
    "Percentage of budget used for each key",
    ["team_id", "team_name", "key_alias"]
)

team_total_spend = Gauge(
    "team_total_spend",
    "Total spend across all keys in a team for the current budget period",
    ["team_id", "team_name"]
)

# Track active team labels to zero out metrics for inactive teams
active_team_labels = set()

async def get_team_keys_by_region(db: Session, team_id: int) -> Dict[DBRegion, List[DBPrivateAIKey]]:
    """
    Get all keys for a team grouped by region.

    Args:
        db: Database session
        team_id: ID of the team to get keys for

    Returns:
        Dictionary mapping regions to lists of keys
    """
    # Get all keys for the team with their regions
    team_users = db.query(DBUser).filter(DBUser.team_id == team_id).all()
    team_user_ids = [user.id for user in team_users]
    # Return keys owned by users in the team OR owned by the team
    team_keys = db.query(DBPrivateAIKey).filter(
        (DBPrivateAIKey.owner_id.in_(team_user_ids)) |
        (DBPrivateAIKey.team_id == team_id)
    ).all()

    # Group keys by region
    keys_by_region = defaultdict(list)
    for key in team_keys:
        if not key.litellm_token:
            logger.warning(f"Key {key.id} has no LiteLLM token, skipping")
            continue
        if not key.region:
            logger.warning(f"Key {key.id} has no region, skipping")
            continue
        keys_by_region[key.region].append(key)

    return keys_by_region

async def handle_stripe_event_background(event, db: Session):
    """
    Background task to handle Stripe webhook events.
    This runs in a separate thread to avoid blocking the webhook response.
    """
    try:
        event_type = event.type
        if not event_type in KNOWN_EVENTS:
            logger.info(f"Unknown event type: {event_type}")
            return
        event_object = event.data.object
        customer_id = event_object.customer
        if not customer_id:
            logger.warning(f"No customer ID found in event, cannot complete processing")
            return
        # Success Events
        if event_type in SUBSCRIPTION_SUCCESS_EVENTS:
            # new subscription
            product_id = await get_product_id_from_subscription(event_object.id)
            start_date = datetime.fromtimestamp(event_object.start_date, tz=UTC)
            await apply_product_for_team(db, customer_id, product_id, start_date)
        elif event_type in INVOICE_SUCCESS_EVENTS:
            # subscription renewed
            subscription = event_object.parent.subscription_details.subscription
            product_id = await get_product_id_from_subscription(subscription)
            start_date = datetime.fromtimestamp(event_object.period_start, tz=UTC)
            await apply_product_for_team(db, customer_id, product_id, start_date)
        # Failure Events
        elif event_type in SESSION_FAILURE_EVENTS:
            product_id = await get_product_id_from_session(event_object.id)
            await remove_product_from_team(db, customer_id, product_id)
        elif event_type in SUBSCRIPTION_FAILURE_EVENTS:
            product_id = await get_product_id_from_subscription(event_object.id)
            await remove_product_from_team(db, customer_id, product_id)
        elif event_type in INVOICE_FAILURE_EVENTS:
            # We assume that the invoice is related to a subscription
            subscription = event_object.parent.subscription_details.subscription
            product_id = await get_product_id_from_subscription(subscription)
            await remove_product_from_team(db, customer_id, product_id)
    except Exception as e:
        logger.error(f"Error in background event handler: {str(e)}")


async def apply_product_for_team(db: Session, customer_id: str, product_id: str, start_date: datetime):
    """
    Apply a product to a team and update their last payment date.
    Also extends all team keys and sets their max budgets via LiteLLM service.

    Args:
        db: Database session
        customer_id: Stripe customer ID
        product_id: Product ID from the database

    Returns:
        bool: True if update was successful, False otherwise
    """
    logger.info(f"Applying product {product_id} to team {customer_id}")
    try:
        # Find the team and product
        team = db.query(DBTeam).filter(DBTeam.stripe_customer_id == customer_id).first()
        product = db.query(DBProduct).filter(DBProduct.id == product_id).first()

        if not team:
            logger.error(f"Team not found for customer ID: {customer_id}")
            return
        if not product:
            logger.error(f"Product not found for ID: {product_id}")
            return

        # Update the last payment date
        team.last_payment = start_date

        # Check if the product is already active for the team
        existing_association = db.query(DBTeamProduct).filter(
            DBTeamProduct.team_id == team.id,
            DBTeamProduct.product_id == product.id
        ).first()

        # Only create new association if it doesn't exist
        if not existing_association:
            team_product = DBTeamProduct(
                team_id=team.id,
                product_id=product.id
            )
            db.add(team_product)
            db.commit()  # Commit the product association

        days_left_in_period, max_max_spend, max_rpm_limit = get_token_restrictions(db, team.id)

        # Get all keys for the team grouped by region
        keys_by_region = await get_team_keys_by_region(db, team.id)

        # Update keys for each region
        for region, keys in keys_by_region.items():
            # Initialize LiteLLM service for this region
            litellm_service = LiteLLMService(
                api_url=region.litellm_api_url,
                api_key=region.litellm_api_key
            )

            # Update each key's duration and budget via LiteLLM
            for key in keys:
                try:
                    await litellm_service.set_key_restrictions(
                        litellm_token=key.litellm_token,
                        duration=f"{days_left_in_period}d",
                        budget_duration=f"{days_left_in_period}d",
                        budget_amount=max_max_spend,
                        rpm_limit=max_rpm_limit
                    )
                except Exception as e:
                    logger.error(f"Failed to update key {key.id} via LiteLLM: {str(e)}")
                    # Continue with other keys even if one fails
                    continue

        db.commit()

    except Exception as e:
        db.rollback()
        logger.error(f"Error applying product to team: {str(e)}")
        raise e

async def remove_product_from_team(db: Session, customer_id: str, product_id: str):
    logger.info(f"Removing product {product_id} from team {customer_id}")
    try:
        # Find the team and product
        team = db.query(DBTeam).filter(DBTeam.stripe_customer_id == customer_id).first()
        product = db.query(DBProduct).filter(DBProduct.id == product_id).first()

        if not team:
            logger.error(f"Team not found for customer ID: {customer_id}")
            return
        if not product:
            logger.error(f"Product not found for ID: {product_id}")
            return
        # Check if the product is already active for the team
        existing_association = db.query(DBTeamProduct).filter(
            DBTeamProduct.team_id == team.id,
            DBTeamProduct.product_id == product.id
        ).first()
        if not existing_association:
            logger.error(f"Product {product_id} not found for team {customer_id}")
            return
        # Remove the product association
        db.delete(existing_association)

    # TODO: Send notification
    # TODO: Expire keys if applicable
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Error removing product from team: {str(e)}")
        raise e

async def monitor_teams(db: Session):
    """
    Daily monitoring task for teams that:
    1. Posts age metrics for teams (since creation for teams without products, since last payment for teams with products)
    2. Sends notifications for teams approaching expiration (25-30 days)
    3. Posts metrics for expired teams (>30 days)
    4. Monitors key spend and notifies if approaching limits
    """
    logger.info("Monitoring teams")
    try:
        # Initialize SES service
        ses_service = SESService()

        # Get all teams
        teams = db.query(DBTeam).all()
        current_time = datetime.now(UTC)

        # Track current active team labels
        current_team_labels = set()

        logger.info(f"Found {len(teams)} teams to track")
        for team in teams:
            team_label = (str(team.id), team.name)
            current_team_labels.add(team_label)

            # Check if team has any products
            has_products = db.query(DBTeamProduct).filter(
                DBTeamProduct.team_id == team.id
            ).first() is not None

            # Calculate team age based on whether they have products
            if has_products and team.last_payment:
                team_age = (current_time - team.last_payment).days
            else:
                team_age = (current_time - team.created_at).days

            if team_age < 0:
                logger.warning(f"Team {team.name} (ID: {team.id}) has a negative age: {team_age} days")
                team_age = 0

            # Post age metric
            team_freshness_days.labels(
                team_id=str(team.id),
                team_name=team.name
            ).set(team_age)

            # Check for notification conditions for teams without products
            logger.info(f"Team {team.name} (ID: {team.id}) has products: {has_products}")
            if not has_products:
                if 25 <= team_age <= 30:
                    days_remaining = 30 - team_age
                    logger.warning(f"Team {team.name} (ID: {team.id}) is approaching expiration in {days_remaining} days")

                    # Send expiration notification email
                    try:
                        template_data = {
                            "team_name": team.name,
                            "days_remaining": days_remaining,
                            "dashboard_url": generate_team_admin_pricing_url(db, team)
                        }

                        if team.admin_email:
                            ses_service.send_email(
                                to_addresses=[team.admin_email],
                                template_name="team-expiring",
                                template_data=template_data
                            )
                            logger.info(f"Sent expiration notification email to team {team.name} (ID: {team.id})")
                        else:
                            logger.warning(f"No email found for team {team.name} (ID: {team.id})")
                    except Exception as e:
                        logger.error(f"Failed to send expiration notification email to team {team.name}: {str(e)}")
                elif team_age > 30:
                    # Post expired metric
                    team_expired_metric.labels(
                        team_id=str(team.id),
                        team_name=team.name
                    ).inc()
                    logger.warning(f"Team {team.name} (ID: {team.id}) has expired without products")

            # Get all keys for the team grouped by region
            keys_by_region = await get_team_keys_by_region(db, team.id)

            # Track total spend across all keys for this team
            team_total = 0

            # Monitor keys for each region
            for region, keys in keys_by_region.items():
                try:
                    # Initialize LiteLLM service for this region
                    litellm_service = LiteLLMService(
                        api_url=region.litellm_api_url,
                        api_key=region.litellm_api_key
                    )

                    # Check spend for each key in this region
                    for key in keys:
                        try:
                            # Get current spend using get_key_info
                            key_info = await litellm_service.get_key_info(key.litellm_token)
                            info = key_info.get("info", {})
                            current_spend = info.get("spend", 0)
                            budget = info.get("max_budget", 0)
                            key_alias = info.get("key_alias", f"key-{key.id}")  # Fallback to key-{id} if no alias

                            # Add to team total
                            team_total += current_spend

                            # Calculate and post percentage used
                            if budget > 0:
                                percentage_used = (current_spend / budget) * 100
                                key_spend_percentage.labels(
                                    team_id=str(team.id),
                                    team_name=team.name,
                                    key_alias=key_alias
                                ).set(percentage_used)

                                # Log warning if approaching limit
                                if percentage_used >= 80:
                                    logger.warning(
                                        f"Key {key_alias} for team {team.name} is approaching spend limit: "
                                        f"${current_spend:.2f} of ${budget:.2f} ({percentage_used:.1f}%)"
                                    )
                            else:
                                # Set to 0 if no budget is set
                                key_spend_percentage.labels(
                                    team_id=str(team.id),
                                    team_name=team.name,
                                    key_alias=key_alias
                                ).set(0)

                        except Exception as e:
                            logger.error(f"Error monitoring key {key.id} spend: {str(e)}")
                            continue

                except Exception as e:
                    logger.error(f"Error initializing LiteLLM service for region {region.name}: {str(e)}")
                    continue

            # Set the total spend metric for the team
            team_total_spend.labels(
                team_id=str(team.id),
                team_name=team.name
            ).set(team_total)

        # Zero out metrics for teams that are no longer active
        for old_label in active_team_labels - current_team_labels:
            team_freshness_days.labels(
                team_id=old_label[0],
                team_name=old_label[1]
            ).set(0)

        # Update active team labels for next run
        active_team_labels.clear()
        active_team_labels.update(current_team_labels)

    except Exception as e:
        logger.error(f"Error in team monitoring task: {str(e)}")
        raise e

def generate_team_admin_token(db: Session, team: DBTeam) -> str:
    """
    Generate a JWT token that authorizes the bearer as an administrator of the team.
    The token is valid for 2 days.

    Args:
        db: Database session
        team: The team object to generate the token for

    Returns:
        str: The generated JWT token

    Raises:
        ValueError: If no admin user is found for the team
    """
    # Find a team admin user
    admin_user = db.query(DBUser).filter(
        DBUser.team_id == team.id,
        DBUser.role == "admin"
    ).first()

    if not admin_user:
        raise ValueError(f"No admin user found for team {team.name} (ID: {team.id})")

    # Create token payload with team admin claims
    payload = {
        "sub": admin_user.email,  # Subject is the admin user's email
        "exp": datetime.now(UTC) + timedelta(days=2)  # Valid for 2 days
    }

    # Generate the token
    token = create_access_token(
        data=payload,
        expires_delta=timedelta(days=2)
    )

    return token

def generate_team_admin_pricing_url(db: Session, team: DBTeam) -> str:
    """
    Generate a URL for the team admin pricing page with a JWT token.

    Args:
        db: Database session
        team: The team object to generate the URL for

    Returns:
        str: The generated URL with the JWT token
    """
    # Generate the token
    token = generate_team_admin_token(db, team)

    # Get the frontend URL from settings
    base_url = "http://localhost:3000" #TODO: Fix this
    path = '/team-admin/pricing'
    url = urljoin(base_url, path)

    # Add the token as a query parameter
    return f"{url}?token={token}"

