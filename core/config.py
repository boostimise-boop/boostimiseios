"""The online services Boostimise talks to (the same ones as the website, boostimise.com).

  Supabase       accounts (Google sign-in), daily quota, plans, history, Realtime updates
  Enhancement    the AI photo enhancement server (bmapi.boostimise.com)

The Supabase key is the *publishable* key: it is public by design (the website ships it to every
browser); Row Level Security and the database functions in boostimise/supabase/schema.sql do the
checks. Never put the secret (service role) key here.

Environment variables override the defaults (for a test server):
  BOOSTIMISE_SUPABASE_URL, BOOSTIMISE_SUPABASE_KEY, BOOSTIMISE_API_URL
"""
from __future__ import annotations

import os

SUPABASE_URL = (os.environ.get("BOOSTIMISE_SUPABASE_URL") or "https://pfdpgttxobqcirfzgrjo.supabase.co").rstrip("/")
SUPABASE_KEY = os.environ.get("BOOSTIMISE_SUPABASE_KEY") or "sb_publishable_2kuJo7V-RnyDYp6mNTDvdA_UH-R0bJT"
API_BASE = (os.environ.get("BOOSTIMISE_API_URL") or "https://bmapi.boostimise.com/").rstrip("/")

# Google sign-in comes back to the app on this address (a tiny local web server that only runs
# while signing in). It must be listed in Supabase: Authentication -> URL Configuration -> Redirect URLs.
AUTH_PORT = 53214
AUTH_REDIRECT = f"http://127.0.0.1:{AUTH_PORT}/auth/callback"

SITE_URL = "https://boostimise.com"
TERMS_URL = f"{SITE_URL}/terms"
PRIVACY_URL = f"{SITE_URL}/privacy"
