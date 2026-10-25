"""The online services Boostimise talks to (the same ones as the website, boostimise.com).

  Supabase       accounts (Google sign-in), daily quota, plans, history, Realtime updates
  Enhancement    the AI photo enhancement server. Its address comes from Supabase (app_settings.api_base,
                 set with `npm run set-api` in the admin folder), so a new server needs no new app;
                 bmapi.boostimise.com until the app has read it.

The Supabase key is the *publishable* key: it is public by design (the website ships it to every
browser); Row Level Security and the database functions in boostimise/supabase/schema.sql do the
checks. Never put the secret (service role) key here.

Environment variables override the defaults (for a test server; BOOSTIMISE_API_URL also wins over Supabase):
  BOOSTIMISE_SUPABASE_URL, BOOSTIMISE_SUPABASE_KEY, BOOSTIMISE_API_URL
"""
from __future__ import annotations

import os
import time

SUPABASE_URL = (os.environ.get("BOOSTIMISE_SUPABASE_URL") or "https://pfdpgttxobqcirfzgrjo.supabase.co").rstrip("/")
SUPABASE_KEY = os.environ.get("BOOSTIMISE_SUPABASE_KEY") or "sb_publishable_2kuJo7V-RnyDYp6mNTDvdA_UH-R0bJT"
API_BASE = (os.environ.get("BOOSTIMISE_API_URL") or "https://bmapi.boostimise.com/").rstrip("/")
_api = {"base": API_BASE, "checked": 0.0}



def api_base() -> str:
    """The enhancement server to use now (the last one read from Supabase)."""
    return _api["base"]


def refresh_api_base(max_age: float = 60.0) -> str:
    """Reads app_settings.api_base when the last read is older than ``max_age`` seconds. Blocking:
    call it from a worker thread. Keeps the current address when Supabase can't be reached."""
    if os.environ.get("BOOSTIMISE_API_URL") or time.monotonic() - _api["checked"] < max_age:
        return _api["base"]
    from . import supabase
    try:
        rows = supabase.select("app_settings", "select=api_base&id=eq.1")
    except Exception:
        return _api["base"]
    _api["checked"] = time.monotonic()
    _api["base"] = ((rows[0].get("api_base") if rows else None) or API_BASE).strip().rstrip("/")
    return _api["base"]


# Google sign-in comes back to the app on this address (a tiny local web server that only runs
# while signing in). It must be listed in Supabase: Authentication -> URL Configuration -> Redirect URLs.
AUTH_PORT = 53214
AUTH_REDIRECT = f"http://127.0.0.1:{AUTH_PORT}/auth/callback"

SITE_URL = "https://boostimise.com"
TERMS_URL = f"{SITE_URL}/terms"
PRIVACY_URL = f"{SITE_URL}/privacy"
