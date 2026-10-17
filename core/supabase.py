"""A small Supabase client: the REST API (tables and the database functions) and Auth (Google
sign-in with PKCE, refreshing and ending sessions). Plain blocking calls - run them with
net.run_bg(). Realtime is in core/realtime.py.

Every write goes through a Postgres function in boostimise/supabase/schema.sql that checks the
caller (auth.uid()), and RLS limits reads to the user's own rows, exactly as for the website.
"""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
import urllib.parse
from typing import Any

from . import theme
from .config import SUPABASE_KEY, SUPABASE_URL
from .net import HttpError, request


class AuthRejected(Exception):
    """Supabase refused the saved sign-in (signed out elsewhere, user deleted, token reused)."""


def headers(token: str | None = None) -> dict[str, str]:
    # A publishable key (sb_publishable_...) is not a JWT: it goes in `apikey` only; the
    # Authorization header carries the user's session when there is one.
    h = {"apikey": SUPABASE_KEY, "X-Client-Info": f"boostimise-desktop/{theme.APP_VERSION}"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    elif not SUPABASE_KEY.startswith("sb_"):
        h["Authorization"] = f"Bearer {SUPABASE_KEY}"  # legacy anon JWT key
    return h


# ---- REST ------------------------------------------------------------------------------------
def rpc(fn: str, params: dict | None = None, token: str | None = None, timeout: float = 20.0) -> Any:
    """Calls a database function (POST /rest/v1/rpc/<fn>) and returns its result."""
    resp = request("POST", f"{SUPABASE_URL}/rest/v1/rpc/{fn}", headers=headers(token),
                   json_body=params or {}, timeout=timeout)
    return resp.json() if resp.data else None


def select(table: str, query: str, token: str | None = None, timeout: float = 20.0) -> list[dict]:
    """GET /rest/v1/<table>?<query> (PostgREST syntax, e.g. "select=id,name&order=created_at.desc")."""
    return request("GET", f"{SUPABASE_URL}/rest/v1/{table}?{query}", headers=headers(token),
                   timeout=timeout).json() or []


def db_message(e: BaseException, fallback: str) -> str:
    """Readable message from a database function error (raise exception '...'), like the website."""
    m = getattr(e, "message", None) or ""
    if isinstance(e, HttpError) and e.status == 0:
        return "Network error. Please try again."
    if m and not re.search(r"permission denied|function .* does not exist|schema cache|JWT|Request failed", m, re.I):
        return m
    return fallback


def public_limits() -> dict:
    """The daily limits shown before signing in (app_settings is readable without an account)."""
    rows = select("app_settings", "select=guest_daily_limit,user_daily_limit,quota_timezone&id=eq.1")
    row = rows[0] if rows else {}
    return {"guest": row.get("guest_daily_limit", 5), "user": row.get("user_daily_limit"),
            "timezone": row.get("quota_timezone") or "UTC"}


# ---- Auth ------------------------------------------------------------------------------------
def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def google_authorize_url(redirect_to: str, challenge: str) -> str:
    """Where the browser goes to sign in with Google; Supabase sends it back to ``redirect_to``
    with ?code=... (PKCE), which exchange_code() turns into a session."""
    q = urllib.parse.urlencode({
        "provider": "google", "redirect_to": redirect_to,
        "code_challenge": challenge, "code_challenge_method": "s256",
        "prompt": "select_account",  # always offer the account picker, like the website
    })
    return f"{SUPABASE_URL}/auth/v1/authorize?{q}"


def _token(grant: str, body: dict) -> dict:
    try:
        return request("POST", f"{SUPABASE_URL}/auth/v1/token?grant_type={grant}", headers=headers(),
                       json_body=body, timeout=25).json()
    except HttpError as e:
        if e.status in (400, 401, 403, 404, 422):
            raise AuthRejected(e.message) from e
        raise


def exchange_code(code: str, verifier: str) -> dict:
    """PKCE: ?code=... from the redirect + our verifier -> session (access_token, refresh_token, user)."""
    return _token("pkce", {"auth_code": code, "code_verifier": verifier})


def refresh(refresh_token: str) -> dict:
    """A new session for a refresh token (each refresh token works once: keep the new one)."""
    return _token("refresh_token", {"refresh_token": refresh_token})


def logout(access_token: str):
    """Ends this session only (the website stays signed in)."""
    request("POST", f"{SUPABASE_URL}/auth/v1/logout?scope=local", headers=headers(access_token), timeout=10)
