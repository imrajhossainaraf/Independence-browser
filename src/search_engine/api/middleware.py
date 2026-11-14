"""Security and hardening middlewares for the search engine HTTP API (D15)."""

from __future__ import annotations

import collections
import hashlib
import time
import uuid
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

if TYPE_CHECKING:
    from search_engine.config import Settings


class RateLimiter:
    """In-memory sliding-window rate limiter per client identifier."""

    def __init__(self, limit_rpm: int = 60, window_seconds: float = 60.0) -> None:
        self.limit_rpm = max(1, limit_rpm)
        self.window_seconds = window_seconds
        self._history: dict[str, collections.deque[float]] = {}

    def is_allowed(self, client_key: str, now: float | None = None) -> bool:
        t = now if now is not None else time.monotonic()
        q = self._history.setdefault(client_key, collections.deque())
        cutoff = t - self.window_seconds
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= self.limit_rpm:
            return False
        q.append(t)
        return True

    def retry_after(self, client_key: str, now: float | None = None) -> int:
        t = now if now is not None else time.monotonic()
        q = self._history.get(client_key)
        if not q or len(q) < self.limit_rpm:
            return 0
        earliest = q[0]
        remaining = self.window_seconds - (t - earliest)
        return max(1, int(remaining + 0.999))


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Enforces strict security headers (CSP, nosniff, no-referrer, DENY, X-Request-ID)."""

    def __init__(self, app: object, settings: Settings) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.settings = settings

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Generate server-side request ID; never copy unvalidated client ID
        req_id = uuid.uuid4().hex
        request.state.request_id = req_id

        response = await call_next(request)

        # Content-Security-Policy: default-src 'none' per D15
        response.headers["X-Request-ID"] = req_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"

        csp_connect = " 'self'"
        if self.settings.api_cors_enabled and self.settings.api_cors_origins:
            csp_connect += " " + " ".join(self.settings.api_cors_origins)

        response.headers["Content-Security-Policy"] = (
            f"default-src 'none'; img-src 'self' data:; script-src 'self'; "
            f"style-src 'self' 'unsafe-inline'; connect-src{csp_connect}"
        )
        return response


class HostHeaderMiddleware(BaseHTTPMiddleware):
    """Blocks DNS rebinding attacks against local search server by validating Host header."""

    def __init__(self, app: object, allowed_hosts: tuple[str, ...]) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.allowed_hosts = {h.strip().lower() for h in allowed_hosts}

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        host = request.headers.get("host", "").strip().lower()
        if not host:
            return JSONResponse(
                status_code=400,
                content={"error": {"code": "invalid_host", "message": "Host header is missing"}},
            )

        # Match exact or host without port
        host_no_port = host.split(":")[0]
        if host not in self.allowed_hosts and host_no_port not in self.allowed_hosts:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "code": "invalid_host",
                        "message": f"Host '{host}' is not allowed",
                    }
                },
            )

        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforces per-client rate limiting (60 rpm default) with Retry-After header."""

    def __init__(self, app: object, limiter: RateLimiter) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.limiter = limiter

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Client identifier: hash of client IP or authorization token
        auth = request.headers.get("authorization", "")
        if auth:
            client_key = hashlib.sha256(auth.encode()).hexdigest()[:16]
        else:
            client_ip = request.client.host if request.client and request.client.host else "unknown"
            client_key = hashlib.sha256(client_ip.encode()).hexdigest()[:16]

        if not self.limiter.is_allowed(client_key):
            retry = self.limiter.retry_after(client_key)
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry)},
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": "Too many requests. Please slow down.",
                    }
                },
            )

        return await call_next(request)


class CorsMiddleware(BaseHTTPMiddleware):
    """Exact-origin CORS handling (wildcard '*' forbidden per D15)."""

    def __init__(
        self,
        app: object,
        enabled: bool,
        allowed_origins: tuple[str, ...],
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.enabled = enabled
        self.allowed_origins = {o.strip().rstrip("/") for o in allowed_origins if o.strip()}

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not self.enabled:
            return await call_next(request)

        origin = request.headers.get("origin", "").strip().rstrip("/")
        is_allowed = origin in self.allowed_origins

        if request.method == "OPTIONS":
            resp = Response(status_code=204)
            if is_allowed and origin:
                resp.headers["Access-Control-Allow-Origin"] = origin
                resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
                resp.headers["Access-Control-Allow-Headers"] = (
                    "Authorization, Content-Type, X-Request-ID, If-None-Match"
                )
                resp.headers["Access-Control-Max-Age"] = "86400"
            return resp

        response = await call_next(request)
        if is_allowed and origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = (
                "Authorization, Content-Type, X-Request-ID, If-None-Match"
            )
        return response
