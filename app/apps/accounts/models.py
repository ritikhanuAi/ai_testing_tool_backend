from sqlalchemy import Column, String, Boolean, Enum as SQLEnum
from app.common.models import BaseModel
from app.common.constants import UserRole

class User(BaseModel):
    __tablename__ = "users"

    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    role = Column(SQLEnum(UserRole), default=UserRole.TESTER, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
