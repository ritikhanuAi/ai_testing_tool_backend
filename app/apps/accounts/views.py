from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import create_access_token
from app.common.serializers import ResponseEnvelope
from app.apps.accounts.models import User
from app.apps.accounts.serializers import (
    UserRegisterRequest, UserLoginRequest, UserResponse, TokenResponse
)
from app.apps.accounts.services import register_user, authenticate_user
from app.apps.accounts.dependencies import get_current_user

async def register_view(
    data: UserRegisterRequest,
    db: AsyncSession = Depends(get_db)
) -> ResponseEnvelope[UserResponse]:
    user = await register_user(db, data)
    return ResponseEnvelope(
        message="User registered successfully",
        data=UserResponse.model_validate(user)
    )

async def login_view(
    data: UserLoginRequest,
    db: AsyncSession = Depends(get_db)
) -> ResponseEnvelope[TokenResponse]:
    user = await authenticate_user(db, data.email, data.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    token = create_access_token(data={"sub": user.id, "email": user.email, "role": user.role.value})
    payload = TokenResponse(
        access_token=token,
        user=UserResponse.model_validate(user)
    )
    return ResponseEnvelope(
        message="Login successful",
        data=payload
    )

async def me_view(
    current_user: User = Depends(get_current_user)
) -> ResponseEnvelope[UserResponse]:
    return ResponseEnvelope(
        message="Current user profile retrieved",
        data=UserResponse.model_validate(current_user)
    )
