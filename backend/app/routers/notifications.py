"""Notification API routes."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.notification import Notification, NotificationType
from app.services.auth import get_current_user
from app.schemas.notification import NotificationResponse, NotificationListResponse, NotificationMarkReadResponse

router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


def _notification_response(n: Notification) -> NotificationResponse:
    return NotificationResponse(
        id=n.id,
        user_id=n.user_id,
        tenant_id=n.tenant_id,
        title=n.title,
        content=n.content,
        type=n.type.value,
        link=n.link,
        read=n.read,
        read_at=n.read_at.isoformat() if n.read_at else None,
        created_at=n.created_at.isoformat() if n.created_at else None,
    )


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    unread_only: bool = Query(False),
    search: str | None = Query(None),
    type: str | None = Query(None),
    read: bool | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List notifications for the current user."""
    filters = [Notification.user_id == current_user.id]
    if unread_only:
        filters.append(Notification.read.is_(False))
    elif read is not None:
        filters.append(Notification.read.is_(read))
    if search and search.strip():
        filters.append(Notification.title.ilike(f"%{search.strip()}%"))
    if type in (t.value for t in NotificationType):
        filters.append(Notification.type == type)

    count_q = select(func.count()).select_from(Notification).where(*filters)
    total = (await db.execute(count_q)).scalar() or 0

    unread_count_q = select(func.count()).select_from(Notification).where(
        Notification.user_id == current_user.id,
        Notification.read.is_(False),
    )
    unread_count = (await db.execute(unread_count_q)).scalar() or 0

    items_q = (
        select(Notification)
        .where(*filters)
        .order_by(Notification.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    items = (await db.execute(items_q)).scalars().all()

    return NotificationListResponse(
        items=[_notification_response(n) for n in items],
        total=total,
        page=page,
        size=size,
        unread_count=unread_count,
    )


@router.get("/unread-count")
async def get_unread_count(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the number of unread notifications for the current user."""
    count_q = select(func.count()).select_from(Notification).where(
        Notification.user_id == current_user.id,
        Notification.read.is_(False),
    )
    count = (await db.execute(count_q)).scalar() or 0
    return {"unread_count": count}


@router.patch("/{notification_id}/read", response_model=NotificationMarkReadResponse)
async def mark_notification_read(
    notification_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark a single notification as read."""
    notification = await db.get(Notification, notification_id)
    if not notification:
        raise HTTPException(404, "通知不存在")
    if notification.user_id != current_user.id:
        raise HTTPException(403, "无权访问")

    notification.read = True
    notification.read_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    return NotificationMarkReadResponse(id=notification.id, read=True)


@router.patch("/read-all")
async def mark_all_notifications_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark all notifications for the current user as read."""
    result = await db.execute(
        select(Notification).where(
            Notification.user_id == current_user.id,
            Notification.read.is_(False),
        )
    )
    notifications = result.scalars().all()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for n in notifications:
        n.read = True
        n.read_at = now
    await db.commit()
    return {"marked_as_read": len(notifications)}


@router.delete("/{notification_id}")
async def delete_notification(
    notification_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a single notification for the current user."""
    notification = await db.get(Notification, notification_id)
    if not notification:
        raise HTTPException(404, "通知不存在")
    if notification.user_id != current_user.id:
        raise HTTPException(403, "无权访问")
    await db.delete(notification)
    await db.commit()
    return {"id": notification_id, "deleted": True}
