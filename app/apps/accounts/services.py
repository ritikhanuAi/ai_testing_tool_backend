from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from app.apps.accounts.models import User
from app.apps.accounts.serializers import UserRegisterRequest
from app.apps.accounts.selectors import get_user_by_email
from app.core.security import get_password_hash, verify_password, create_access_token
from app.core.exceptions import PlatformException

async def register_user(db: AsyncSession, data: UserRegisterRequest) -> User:
    """Create a new user account with hashed password."""
    existing = await get_user_by_email(db, data.email)
    if existing:
        raise PlatformException(f"User with email '{data.email}' already exists", code="USER_EXISTS", status_code=400)

    user = User(
        email=data.email.lower().strip(),
        hashed_password=get_password_hash(data.password),
        full_name=data.full_name,
        role=data.role
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user

async def authenticate_user(db: AsyncSession, email: str, password: str) -> Optional[User]:
    """Verify credentials and return user if valid."""
    user = await get_user_by_email(db, email)
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user
