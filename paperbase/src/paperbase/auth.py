"""Bearer-token auth for the paperbase HTTP API."""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings, get_settings
from .models import BrowserUser
from .security import verify_csrf_token
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


def set_no_store_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _allowed_browser_origins(settings: Settings) -> set[str]:
    origins = {settings.browser_origin.rstrip("/"), settings.chat_public_url.rstrip("/")}
    return {origin for origin in origins if origin}


def _request_base_origin(request: Request) -> str | None:
    parts = urlsplit(str(request.base_url))
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return None


def _request_origin(request: Request) -> str | None:
    origin = request.headers.get("origin")
    if origin:
        return origin.rstrip("/")
    referer = request.headers.get("referer")
    if not referer:
        return None
    parts = urlsplit(referer)
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return None


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


def require_browser_write_user(
    request: Request,
    settings: Settings = Depends(get_settings),
    service: PaperbaseService = Depends(get_service),
) -> BrowserUser:
    raw_token = request.cookies.get(settings.browser_session_cookie_name)
    user = service.get_current_browser_user(raw_token)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    request_origin = _request_origin(request)
    allowed_origins = _allowed_browser_origins(settings)
    request_base_origin = _request_base_origin(request)
    if request_base_origin:
        allowed_origins.add(request_base_origin.rstrip("/"))
    if request_origin and request_origin not in allowed_origins:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cross-site browser requests are not allowed")

    csrf_header = request.headers.get("x-paperbase-csrf")
    if not verify_csrf_token(raw_token, csrf_header, settings):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")

    return user
