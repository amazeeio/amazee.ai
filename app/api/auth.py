from typing import Optional, List, Union
import email_validator
from fastapi import APIRouter, Depends, HTTPException, status, Response, Request, Form
from email_validator import validate_email, EmailNotValidError
from sqlalchemy.orm import Session
import logging
from logging.handlers import TimedRotatingFileHandler
import secrets
import os
from urllib.parse import urlparse
from pathlib import Path

from app.db.database import get_db
from app.schemas.models import (
    Token,
    User,
    UserCreate,
    APIToken,
    APITokenCreate,
    APITokenResponse,
    UserUpdate,
    EmailValidation,
    LoginData,
    SignInData,
    TeamCreate
)
from app.db.models import (
    DBUser, DBAPIToken
)
from app.core.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    get_current_user_from_auth,
)
from app.services.dynamodb import DynamoDBService
from app.services.ses import SESService
from app.api.teams import register_team

# # Configure auth logger - disabled until we make it work in lagoon
# auth_logger = logging.getLogger("auth")
# auth_logger.setLevel(logging.INFO)
#
# # Create logs directory if it doesn't exist
# log_dir = Path("logs")
# log_dir.mkdir(exist_ok=True)
#
# # Configure file handler with daily rotation
# file_handler = TimedRotatingFileHandler(
#     filename=log_dir / "auth.log",
#     when="midnight",
#     interval=1,
#     backupCount=30,  # Keep logs for 30 days
#     encoding="utf-8"
# )
#
# # Configure formatter
# formatter = logging.Formatter(
#     "%(asctime)s - %(levelname)s - %(message)s",
#     datefmt="%Y-%m-%d %H:%M:%S"
# )
# file_handler.setFormatter(formatter)
#
# # Add handler to logger
# auth_logger.addHandler(file_handler)

auth_logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Authentication"]
)

def get_cookie_domain():
    """Extract domain from COOKIE_DOMAIN or LAGOON_ROUTES for cookie settings."""
    # First check for explicit cookie domain setting
    cookie_domain = os.getenv("COOKIE_DOMAIN")
    if cookie_domain:
        return cookie_domain

    # Fall back to extracting from LAGOON_ROUTES
    lagoon_routes = os.getenv("LAGOON_ROUTES")
    if not lagoon_routes:
        return None

    # Take first URL from comma-separated list
    first_url = lagoon_routes.split(',')[0]
    # Parse the URL and get the hostname
    hostname = urlparse(first_url).netloc
    # Remove the first part (e.g., 'backend' or 'frontend')
    domain = '.'.join(hostname.split('.')[1:])
    return domain

async def get_login_data(
    request: Request,
    username: Optional[str] = Form(None),
    password: Optional[str] = Form(None),
) -> Union[LoginData, None]:
    if username and password:
        return LoginData(username=username, password=password)

    if request.headers.get("content-type", "").lower() == "application/json":
        try:
            body = await request.json()
            return LoginData(**body)
        except Exception:
            return None
    return None

async def get_sign_in_data(
    request: Request,
    username: Optional[str] = Form(None),
    verification_code: Optional[str] = Form(None),
) -> Union[SignInData, None]:
    if username and verification_code:
        return SignInData(username=username, verification_code=verification_code)

    if request.headers.get("content-type", "").lower() == "application/json":
        try:
            body = await request.json()
            return SignInData(**body)
        except Exception:
            return None
    return None

def create_and_set_access_token(response: Response, user_email: str) -> Token:
    """
    Create an access token for the user and set it as a cookie.

    Args:
        response: The FastAPI response object to set the cookie on
        user_email: The email of the user to create the token for

    Returns:
        Token: The created access token
    """
    # Create access token
    access_token = create_access_token(
        data={"sub": user_email}
    )

    # Get cookie domain from LAGOON_ROUTES
    cookie_domain = get_cookie_domain()

    # Prepare cookie settings
    cookie_settings = {
        "key": "access_token",
        "value": access_token,
        "httponly": True,
        "max_age": 1800,
        "expires": 1800,
        "samesite": 'none',
        "secure": True,
        "path": '/',
    }

    # Only set domain if we got one from LAGOON_ROUTES
    if cookie_domain:
        cookie_settings["domain"] = cookie_domain

    # Set cookie with appropriate settings
    response.set_cookie(**cookie_settings)

    return {"access_token": access_token, "token_type": "bearer"}

@router.post("/login", response_model=Token)
async def login(
    request: Request,
    response: Response,
    login_data: Optional[LoginData] = Depends(get_login_data),
    db: Session = Depends(get_db)
):
    """
    Login to get access to the API.

    Accepts both application/x-www-form-urlencoded and application/json formats.

    Form data:
    - **username**: Your email address
    - **password**: Your password

    JSON data:
    - **username**: Your email address
    - **password**: Your password

    On successful login, an access token will be set as an HTTP-only cookie and also returned in the response.
    Use this token for subsequent authenticated requests.
    """
    if not login_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid login data. Please provide username and password in either form data or JSON format."
        )

    auth_logger.info(f"Login attempt for user: {login_data.username}")
    user = db.query(DBUser).filter(DBUser.email == login_data.username).first()
    if not user or not verify_password(login_data.password, user.hashed_password):
        auth_logger.warning(f"Failed login attempt for user: {login_data.username}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password"
        )

    auth_logger.info(f"Successful login for user: {login_data.username}")
    return create_and_set_access_token(response, user.email)

@router.post("/logout")
async def logout(response: Response):
    # Get cookie domain for logout
    cookie_domain = get_cookie_domain()

    # Prepare cookie deletion settings
    delete_settings = {
        "key": "access_token",
        "path": "/",
        "secure": True,
        "samesite": 'none',
    }

    # Only set domain if we got one from LAGOON_ROUTES
    if cookie_domain:
        delete_settings["domain"] = cookie_domain

    # Delete cookie with appropriate settings
    response.delete_cookie(**delete_settings)
    return {"message": "Successfully logged out"}

@router.get("/me", response_model=User)
async def read_users_me(current_user: DBUser = Depends(get_current_user_from_auth)):
    return current_user

@router.put("/me/update", response_model=User)
async def update_user_me(
    user_update: UserUpdate,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    """
    Update the current user's profile.

    - To update email: provide new email and current_password
    - To update password: provide current_password and new_password
    """
    # Always verify current password if provided
    if user_update.current_password is not None:
        if not verify_password(user_update.current_password, current_user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Incorrect password"
            )

    # Handle password update
    if user_update.new_password is not None:
        if user_update.current_password is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is required to update password"
            )
        current_user.hashed_password = get_password_hash(user_update.new_password)

    # Handle email update
    if user_update.email is not None and user_update.email != current_user.email:
        if user_update.current_password is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is required to update email"
            )
        # Check if email is already taken
        existing_user = db.query(DBUser).filter(
            DBUser.email == user_update.email,
            DBUser.id != current_user.id
        ).first()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )
        current_user.email = user_update.email

    db.commit()
    db.refresh(current_user)
    return current_user

@router.post("/register", response_model=User)
async def register(
    request: Request,
    user: UserCreate,
    db: Session = Depends(get_db)
):
    """
    Register a new user account.

    - **email**: Your email address
    - **password**: A secure password (minimum 8 characters)

    After registration, you'll need to login to get an access token.
    """
    auth_logger.info(f"Registration attempt for user: {user.email}")
    # Check if user with this email exists
    db_user = db.query(DBUser).filter(DBUser.email == user.email).first()
    if db_user:
        auth_logger.warning(f"Registration failed - email already exists: {user.email}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )

    # Create new user
    hashed_password = get_password_hash(user.password)
    db_user = DBUser(
        email=user.email,
        hashed_password=hashed_password,
        is_admin=False  # Force is_admin to be False for all new registrations
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    auth_logger.info(f"Successfully registered new user: {user.email}")
    return db_user

@router.post("/validate-email")
async def validate_email(
    request: Request,
    email_data: Optional[EmailValidation] = None,
    email: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """
    Validate an email address and generate a validation code.

    Accepts both application/x-www-form-urlencoded and application/json formats.

    Form data:
    - **email**: The email address to validate

    JSON data:
    - **email**: The email address to validate

    Returns a success message if the email is valid and a code has been generated.
    """
    # Handle both JSON and form data
    if email_data:
        email = email_data.email
    elif not email:
        if request.headers.get("content-type", "").lower() == "application/json":
            try:
                body = await request.json()
                email = body.get("email")
            except Exception:
                pass

    if not email:
        auth_logger.warning("Email validation attempt with no email provided")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is required"
        )

    auth_logger.info(f"Email validation attempt for: {email}")
    try:
        email_validator.validate_email(email, check_deliverability=False)
    except EmailNotValidError as e:
        auth_logger.warning(f"Invalid email format for {email}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid email format: {e}"
        )

    # Generate and store validation code
    code = generate_validation_token(email)
    user = db.query(DBUser).filter(DBUser.email == email).first()
    if user:
        email_template = 'returning-user-code'
        auth_logger.info(f"Sending validation code to existing user: {email}")
    else:
        email_template = 'new-user-code'
        auth_logger.info(f"Sending validation code to new user: {email}")

    # Send the validation code via email
    ses_service = SESService()
    email_sent = ses_service.send_email(
        to_addresses=[email],
        template_name=email_template,
        template_data={
            'code': code
        }
    )

    if not email_sent:
        auth_logger.error(f"Failed to send validation code email to {email}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send validation code email"
        )

    auth_logger.info(f"Successfully sent validation code to: {email}")
    return {
        "message": "Validation code has been generated and sent"
    }

def generate_token() -> str:
    return secrets.token_urlsafe(32)

def generate_validation_token(email: str) -> str:
    """
    Generate a validation token for the given email and store it in DynamoDB.

    Args:
        email (str): The email address to generate a token for

    Returns:
        str: The generated validation token (8 characters, alphanumeric, uppercase)
    """
    # Generate an 8-character alphanumeric code in uppercase
    code = ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(8))

    # Store the code in DynamoDB
    dynamodb_service = DynamoDBService()
    dynamodb_service.write_validation_code(email, code)

    return code

# API Token routes (as apposed to AI Token routes)
@router.post("/token", response_model=APIToken)
async def create_token(
    token_create: APITokenCreate,
    current_user = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    db_token = DBAPIToken(
        name=token_create.name,
        token=generate_token(),
        user_id=current_user.id
    )
    db.add(db_token)
    db.commit()
    db.refresh(db_token)
    return db_token

@router.get("/token", response_model=List[APITokenResponse])
async def list_tokens(
    current_user = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    """List all API tokens for the current user"""
    return current_user.api_tokens

@router.delete("/token/{token_id}")
async def delete_token(
    token_id: int,
    current_user = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    """Delete an API token"""
    token = db.query(DBAPIToken).filter(
        DBAPIToken.id == token_id,
        DBAPIToken.user_id == current_user.id
    ).first()
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")
    db.delete(token)
    db.commit()
    return {"message": "Token deleted successfully"}

@router.post("/sign-in", response_model=Token)
async def sign_in(
    request: Request,
    response: Response,
    sign_in_data: Optional[SignInData] = Depends(get_sign_in_data),
    db: Session = Depends(get_db)
):
    """
    Sign in using a verification code instead of a password.

    Accepts both application/x-www-form-urlencoded and application/json formats.

    Form data:
    - **username**: Your email address
    - **verification_code**: The verification code sent to your email

    JSON data:
    - **username**: Your email address
    - **verification_code**: The verification code sent to your email

    On successful sign in, an access token will be set as an HTTP-only cookie and also returned in the response.
    Use this token for subsequent authenticated requests.

    If the user doesn't exist, they will be automatically registered and a new team will be created
    with them as the admin.
    """
    if not sign_in_data:
        auth_logger.warning("Sign-in attempt with invalid data format")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid sign in data. Please provide username and verification code in either form data or JSON format."
        )

    auth_logger.info(f"Sign-in attempt for user: {sign_in_data.username}")
    # Verify the code using DynamoDB first
    dynamodb_service = DynamoDBService()
    stored_code = dynamodb_service.read_validation_code(sign_in_data.username)

    if not stored_code or stored_code.get('code').upper() != sign_in_data.verification_code.upper():
        auth_logger.warning(f"Invalid verification code for user: {sign_in_data.username}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or verification code"
        )

    # Get user from database after verifying the code
    user = db.query(DBUser).filter(DBUser.email == sign_in_data.username).first()

    # If user doesn't exist, create a new user and team
    if not user:
        auth_logger.info(f"Creating new user and team for: {sign_in_data.username}")
        # First create the team
        team_data = TeamCreate(
            name=f"Team {sign_in_data.username}",
            admin_email=sign_in_data.username,
            phone="",  # Required by schema but not used for auto-created teams
            billing_address=""  # Required by schema but not used for auto-created teams
        )
        team = await register_team(team_data, db)

        # Create new user without password since they're using verification code
        user = DBUser(
            email=sign_in_data.username,
            hashed_password="",  # Empty password since they'll use verification code
            role="admin",  # Set role to admin for new users
            team_id=team.id  # Associate user with the team
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        auth_logger.info(f"Successfully created new user and team for: {sign_in_data.username}")

    auth_logger.info(f"Successful sign-in for user: {sign_in_data.username}")
    return create_and_set_access_token(response, user.email)