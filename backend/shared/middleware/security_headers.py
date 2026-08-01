"""
Security response headers (production-readiness audit finding #15) -
applied to every response via middleware, not per-route, so a route
added later can't silently ship without them.
"""

from typing import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    - X-Content-Type-Options: nosniff - stops browsers from MIME-sniffing
      a response into executing as something other than its declared
      Content-Type (e.g. treating a JSON error body as HTML/script).
    - X-Frame-Options: DENY - this app has no legitimate reason to be
      framed by another origin; blocks clickjacking.
    - Content-Security-Policy: default-src 'self' - this backend serves
      no HTML/templates/static assets of its own (confirmed: no
      HTMLResponse/StaticFiles/Jinja2Templates usage anywhere in this
      codebase, pure JSON API), so the strictest useful policy costs
      nothing.
    - Strict-Transport-Security - only meaningful, and only sent, when
      this specific request actually arrived over HTTPS. Checked via
      X-Forwarded-Proto (the standard header a reverse proxy/load
      balancer sets in front of this plain-HTTP app in any real
      deployment - see main.py's CORS/debug-route fixes for the same
      "this app is always plain HTTP behind something else" assumption)
      rather than is_production(), which is an environment setting, not
      proof this request itself was served over TLS - forcing HSTS on a
      plain-HTTP dev/CI connection would tell browsers to require HTTPS
      for this origin going forward, breaking local http://localhost
      access outright.
    """

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'"

        if request.headers.get("x-forwarded-proto", request.url.scheme) == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        return response
