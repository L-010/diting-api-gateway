"""无需登录的公开配置与工具目录，仅返回脱敏发布信息。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import ApiEndpoint, PublishedRoute, Tool
from ..services.platform_settings import get_effective_platform_settings


router = APIRouter(prefix="/api/public", tags=["公开门户"])


@router.get("/config")
def public_config(session: Session = Depends(get_db)) -> dict[str, object]:
    settings = get_settings()
    email_settings = get_effective_platform_settings(session)
    return {
        "registration_enabled": email_settings.registration_enabled and email_settings.smtp_configured,
        "password_recovery_enabled": email_settings.smtp_configured,
        "email_change_enabled": email_settings.smtp_configured,
        "support_contact": settings.support_contact,
        "gateway_base_url": email_settings.public_gateway_base_url,
        "site_name": email_settings.site_name,
        "site_subtitle": email_settings.site_subtitle,
        "brand_image_url": email_settings.brand_image_url,
    }


@router.get("/tools")
def public_tools(session: Session = Depends(get_db)) -> list[dict[str, object]]:
    rows = session.execute(
        select(Tool, func.count(PublishedRoute.id))
        .join(PublishedRoute, PublishedRoute.tool_id == Tool.id)
        .join(ApiEndpoint, ApiEndpoint.id == PublishedRoute.endpoint_id)
        .where(
            Tool.is_enabled.is_(True),
            PublishedRoute.is_enabled.is_(True),
            PublishedRoute.access_mode != "admin_only",
            ApiEndpoint.status == "published",
        )
        .group_by(Tool.id)
        .order_by(Tool.name, Tool.slug)
    ).all()
    return [
        {
            "slug": tool.slug,
            "name": tool.name,
            "description": tool.description,
            "published_endpoint_count": int(count),
        }
        for tool, count in rows
    ]


@router.get("/tools/{tool_slug}")
def public_tool(tool_slug: str, session: Session = Depends(get_db)) -> dict[str, object]:
    tool = session.scalar(select(Tool).where(Tool.slug == tool_slug, Tool.is_enabled.is_(True)))
    if not tool:
        raise HTTPException(status_code=404, detail="工具不存在")
    rows = session.execute(
        select(ApiEndpoint, PublishedRoute)
        .join(PublishedRoute, PublishedRoute.endpoint_id == ApiEndpoint.id)
        .where(
            PublishedRoute.tool_id == tool.id,
            PublishedRoute.is_enabled.is_(True),
            PublishedRoute.access_mode != "admin_only",
            ApiEndpoint.status == "published",
        )
        .order_by(PublishedRoute.gateway_path, PublishedRoute.method)
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="工具尚未发布普通用户接口")
    return {
        "slug": tool.slug,
        "name": tool.name,
        "description": tool.description,
        "published_endpoint_count": len(rows),
        "endpoints": [
            {
                "method": route.method,
                "gateway_path": route.gateway_path,
                "summary": endpoint.summary or endpoint.operation_id or "未命名接口",
                "description": endpoint.public_description or "",
            }
            for endpoint, route in rows
        ],
    }
