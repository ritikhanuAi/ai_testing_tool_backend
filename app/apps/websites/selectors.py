from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from app.apps.websites.models import Website, CredentialRef

async def list_websites(db: AsyncSession, active_only: bool = True) -> List[Website]:
    stmt = select(Website)
    if active_only:
        stmt = stmt.where(Website.is_active == True)
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def get_website_by_id(db: AsyncSession, website_id: str) -> Optional[Website]:
    stmt = select(Website).where(Website.id == website_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()

async def get_credential_by_key(db: AsyncSession, website_id: str, key_name: str) -> Optional[CredentialRef]:
    stmt = select(CredentialRef).where(
        CredentialRef.website_id == website_id,
        CredentialRef.key_name == key_name
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
