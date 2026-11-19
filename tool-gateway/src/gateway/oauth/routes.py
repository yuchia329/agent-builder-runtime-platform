"""Public browser routes of the login flow (no run token; a person's browser calls them):

- GET /oauth/<provider>/start?ticket=...: sets the nonce cookie, redirects to the provider.
- GET /oauth/<provider>/callback?code=...&state=...: finishes the login, shows a short page.

Pages never echo a ticket, state, code or token. Every answer is `no-store` and sends no referrer
(the URLs carry one-time values). Without login settings every route answers 503.
"""

import html
import logging

import anyio
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from gateway.oauth.github import OAuthUnavailable
from gateway.oauth.logins import LINK_SECONDS, LoginRefused, OAuthLogins

log = logging.getLogger("gateway.oauth")

COOKIE = "gateway_oauth_nonce"
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def oauth_app(logins: dict[str, OAuthLogins], secure_cookie: bool) -> Starlette:
    def provider(request: Request) -> OAuthLogins | None:
        return logins.get(request.path_params["provider"])

    async def start(request: Request) -> Response:
        flow = provider(request)
        if flow is None:
            return page(404, "Unknown login", "This gateway has no such login.")
        try:
            url, nonce = flow.begin(request.query_params.get("ticket", ""))
        except LoginRefused as e:
            return page(400, "Login link not valid", str(e))
        response = RedirectResponse(url, status_code=302, headers=HEADERS)
        response.set_cookie(
            COOKIE,
            nonce,
            max_age=LINK_SECONDS,
            path=cookie_path(flow),
            httponly=True,
            samesite="lax",  # sent on GitHub's top-level redirect back, not on cross-site posts
            secure=secure_cookie,
        )
        return response

    async def callback(request: Request) -> Response:
        flow = provider(request)
        if flow is None:
            return page(404, "Unknown login", "This gateway has no such login.")
        query = request.query_params
        if query.get("error"):
            reason = "".join(c for c in query["error"] if c.isalnum() or c == "_")[:64]
            return done(flow, page(400, "Login cancelled", f"No account was connected ({reason})."))
        if not query.get("code") or not query.get("state"):
            return page(400, "Login not finished", "The answer from the provider is incomplete.")
        try:
            owner, login = await anyio.to_thread.run_sync(
                flow.finish, query["state"], query["code"], request.cookies.get(COOKIE)
            )
        except LoginRefused as e:
            return page(400, "Login not finished", str(e))
        except OAuthUnavailable as e:
            log.warning("Login callback for %s failed: %s", flow.provider.name, e)
            text = f"{flow.provider.name} is unavailable; try again."
            return page(502, "Login not finished", text)
        return done(
            flow,
            page(
                200,
                "Account connected",
                f"Connected {flow.provider.name} account {login} for {owner}. "
                "You can close this page and run the agent again.",
            ),
        )

    return Starlette(
        routes=[
            Route("/oauth/{provider}/start", start, methods=["GET"]),
            Route("/oauth/{provider}/callback", callback, methods=["GET"]),
        ]
    )


def unavailable_app() -> Starlette:
    async def off(request: Request) -> Response:
        return page(503, "Login not set up", "User login is not set up on this gateway.")

    return Starlette(routes=[Route("/oauth/{rest:path}", off, methods=["GET"])])


def cookie_path(flow: OAuthLogins) -> str:
    return f"/oauth/{flow.provider.name}"


def done(flow: OAuthLogins, response: Response) -> Response:
    response.delete_cookie(COOKIE, path=cookie_path(flow))
    return response


def page(status: int, title: str, text: str) -> HTMLResponse:
    body = (
        f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>"
        "<body style='font-family:sans-serif;max-width:40em;margin:3em auto'>"
        f"<h1>{html.escape(title)}</h1><p>{html.escape(text)}</p>"
    )
    return HTMLResponse(body, status_code=status, headers=HEADERS)
