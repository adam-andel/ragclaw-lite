"""Pydantic schemas for notification API."""

from pydantic import BaseModel, ConfigDict


class NotificationResponse(BaseModel):
    id: str
    user_id: str
    tenant_id: str | None
    title: str
    content: str | None
    type: str
    link: str | None
    read: bool
    read_at: str | None
    created_at: str | None

    model_config = ConfigDict(from_attributes=True)


class NotificationListResponse(BaseModel):
    items: list[NotificationResponse]
    total: int
    page: int
    size: int
    unread_count: int


class NotificationMarkReadResponse(BaseModel):
    id: str
    read: bool
