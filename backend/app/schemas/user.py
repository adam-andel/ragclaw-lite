"""Pydantic schemas for auth & user management."""

from datetime import datetime
from pydantic import BaseModel, Field, EmailStr


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=100)
    password: str = Field(..., min_length=4)


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=100)
    password: str = Field(..., min_length=4)
    display_name: str = Field(default="", max_length=200)
    email: str | None = None
    tenant_id: str | None = None  # optional, auto-generated if not provided


class SetupStatusResponse(BaseModel):
    """Public report of whether the system still needs its first admin."""

    needs_setup: bool


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: "UserResponse"


class RefreshRequest(BaseModel):
    refresh_token: str
    device: str | None = None


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: str
    username: str
    display_name: str
    email: str | None = None
    role: str
    is_active: bool
    avatar_url: str | None = None
    tenant_id: str | None = None
    memory: str | None = None
    timezone: str | None = None
    created_at: datetime
    # Non-blocking config-time warnings, e.g. profile memory eating too large a
    # share of the context window. Only populated when the profile-update
    # endpoint actually rewrote `memory`; every other response leaves it empty.
    # Each entry is {"code": <BARE_CODE>, "params": {...}} -- localized by the
    # frontend.
    warnings: list[dict] = []

    model_config = {"from_attributes": True}


class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int
    page: int
    size: int


class UserUpdateRequest(BaseModel):
    display_name: str | None = None
    email: str | None = None
    role: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=4)
    memory: str | None = None
    # IANA timezone (e.g. "Asia/Shanghai", "America/New_York"). Send null/empty to
    # clear back to the browser-detected/UTC default. Validated structurally in
    # the handler; the REPL sandbox re-validates against its zoneinfo data.
    timezone: str | None = None


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=100)
    password: str = Field(..., min_length=4)
    display_name: str = ""
    email: str | None = None
    role: str = "user"
