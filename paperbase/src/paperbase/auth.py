"""Bearer-token auth for the paperbase HTTP API."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings, get_settings
from .models import BrowserUser
from .service import PaperbaseService, get_service


_bearer = HTTPBearer(auto_error=False)


def require_bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )
    if credentials.credentials != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid bearer token",
        )
    return credentials.credentials


def set_browser_session_cookie(response: Response, raw_token: str, settings: Settings) -> None:
    response.set_cookie(
        key=settings.browser_session_cookie_name,
        value=raw_token,
        httponly=True,
        max_age=settings.browser_session_ttl_seconds,
        secure=settings.browser_session_secure,
        samesite="lax",
        path="/",
    )


def clear_browser_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.browser_session_cookie_name,
        httponly=True,
        secure=settings.browser_session_secure,
        samesite="lax",
        path="/",
    )


def get_optional_browser_user(
    request: Request,
    settings: Settings = Depends(get_settings),
    service: PaperbaseService = Depends(get_service),
) -> BrowserUser | None:
    return service.get_current_browser_user(request.cookies.get(settings.browser_session_cookie_name))


def require_browser_user(
    request: Request,
    settings: Settings = Depends(get_settings),
    service: PaperbaseService = Depends(get_service),
) -> BrowserUser:
    user = service.get_current_browser_user(request.cookies.get(settings.browser_session_cookie_name))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user
