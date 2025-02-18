from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
import secrets
from datetime import datetime

from app.db.database import get_db
from app.schemas.models import APIToken, APITokenCreate
from app.db.models import DBAPIToken
from app.api.auth import get_current_user_from_auth

router = APIRouter()

def generate_token() -> str:
    return secrets.token_urlsafe(32)

@router.post("", response_model=APIToken)
@router.post("/", response_model=APIToken)
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

@router.get("", response_model=List[APIToken])
@router.get("/", response_model=List[APIToken])
async def list_tokens(
    current_user = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    return current_user.api_tokens

@router.delete("/{token_id}")
async def delete_token(
    token_id: int,
    current_user = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    token = db.query(DBAPIToken).filter(
        DBAPIToken.id == token_id,
        DBAPIToken.user_id == current_user.id
    ).first()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found"
        )

    db.delete(token)
    db.commit()
    return {"message": "Token deleted successfully"}