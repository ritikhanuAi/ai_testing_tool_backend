from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from app.common.serializers import BaseSchema
from app.common.constants import UserRole

class UserRegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: Optional[str] = None
    role: Optional[UserRole] = UserRole.TESTER

class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str

class UserResponse(BaseSchema):
    id: str
    email: str
    full_name: Optional[str] = None
    role: UserRole
    is_active: bool

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
