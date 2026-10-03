from __future__ import annotations

from fastapi import Header

from app.core.errors import AuthenticationError
from app.core.security import Principal
from app.database import get_connection
from app.services.auth import AuthService


def current_principal(authorization: str | None = Header(default=None)) -> Principal:
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthenticationError("缺少 Bearer 会话令牌")
    token = authorization[7:].strip()
    if not token:
        raise AuthenticationError("会话令牌为空")
    return AuthService(get_connection()).principal(token)
