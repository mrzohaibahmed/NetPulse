"""HTTP security headers for production responses."""

from __future__ import annotations

from flask import Flask, Response


def register_security_headers(app: Flask) -> None:
    """Attach a conservative baseline of security headers on every response."""

    @app.after_request
    def _set_security_headers(response: Response) -> Response:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        # SPA loads its own assets; keep CSP permissive enough for Vite-built React
        # while blocking mixed plugin/object content. Reverse proxies may tighten further.
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'",
        )
        # HSTS only when request was HTTPS (or X-Forwarded-Proto says so).
        try:
            from flask import request  # noqa: PLC0415

            proto = (request.headers.get("X-Forwarded-Proto") or request.scheme or "").lower()
            if proto == "https":
                response.headers.setdefault(
                    "Strict-Transport-Security",
                    "max-age=31536000; includeSubDomains",
                )
        except Exception:  # noqa: BLE001
            pass
        return response
