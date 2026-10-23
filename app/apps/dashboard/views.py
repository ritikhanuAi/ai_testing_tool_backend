from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from app.core.database import get_db
from app.common.serializers import ResponseEnvelope
from app.apps.dashboard.selectors import get_dashboard_summary, get_recent_activity
from app.apps.dashboard.serializers import DashboardSummaryResponse, RecentActivityItem

async def dashboard_summary_view(
    db: AsyncSession = Depends(get_db)
) -> ResponseEnvelope[DashboardSummaryResponse]:
    """Retrieve high-level statistics and health indicators for dashboard cards."""
    stats = await get_dashboard_summary(db)
    return ResponseEnvelope(
        message="Dashboard summary retrieved successfully",
        data=DashboardSummaryResponse.model_validate(stats)
    )

async def dashboard_recent_activity_view(
    limit: int = 10,
    db: AsyncSession = Depends(get_db)
) -> ResponseEnvelope[List[RecentActivityItem]]:
    """Retrieve recent test run executions for dashboard feeds and tables."""
    activity = await get_recent_activity(db, limit=limit)
    return ResponseEnvelope(
        message="Recent activity retrieved successfully",
        data=[RecentActivityItem.model_validate(item) for item in activity],
        meta={"limit": limit, "count": len(activity)}
    )
