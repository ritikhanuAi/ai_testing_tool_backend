import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, JSON
from sqlalchemy.dialects.postgresql import JSONB
from app.core.database import Base

def utcnow():
    return datetime.now(timezone.utc)

# JSONType defaults to PostgreSQL JSONB when available for fast indexing and binary storage
JSONType = JSON().with_variant(JSONB, "postgresql")

class UUIDModelBase:
    """Base mixin providing UUID primary keys."""
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

class TimestampMixin:
    """Mixin providing created_at and updated_at tracking with timezone."""
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

class BaseModel(UUIDModelBase, TimestampMixin, Base):
    """Abstract declarative base model for application entities."""
    __abstract__ = True
