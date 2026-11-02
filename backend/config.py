# backend/config.py
# Every setting lives here and is read from the environment (.env). Money settings are
# stored as integer cents so the rest of the code never does float arithmetic on money.

import os
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _text(name, default=""):
    return os.getenv(name, default).strip()


def _flag(name, default=False):
    return _text(name, str(default)).lower() in ("1", "true", "yes", "on")


def _int(name, default):
    try:
        return int(_text(name, str(default)))
    except ValueError:
        return default


def _cents(name, default_dollars):
    """Read a dollar amount like '25' or '12.50' and return integer cents."""
    raw = _text(name, str(default_dollars))
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        amount = Decimal(str(default_dollars))
    if not amount.is_finite() or amount < 0:
        amount = Decimal(str(default_dollars))
    return int((amount * 100).to_integral_value(ROUND_HALF_UP))


# ---------- AI ----------
GEMINI_API_KEY = _text("GEMINI_API_KEY")
GEMINI_MODEL = _text("GEMINI_MODEL", "gemini-flash-latest")

# ---------- Voice ----------
VOICE_ENABLED = _flag("VOICE_ENABLED", True)
MAX_AUDIO_SECONDS = _int("MAX_AUDIO_SECONDS", 15)

# ---------- Product search ----------
CHANNEL3_API_KEY = _text("CHANNEL3_API_KEY")
CHANNEL3_BASE_URL = "https://api.trychannel3.com"
USE_DEMO_CATALOG = _flag("USE_DEMO_CATALOG", False)
CATALOG_FALLBACK = _flag("CATALOG_FALLBACK", True)

# ---------- PayPal ----------
PAYPAL_ENV = _text("PAYPAL_ENV", "sandbox").lower()
PAYPAL_CLIENT_ID = _text("PAYPAL_CLIENT_ID")
PAYPAL_CLIENT_SECRET = _text("PAYPAL_CLIENT_SECRET")
PAYPAL_BASE_URL = ("https://api-m.paypal.com" if PAYPAL_ENV == "live"
                   else "https://api-m.sandbox.paypal.com")
PAYPAL_RETURN_URL = _text("PAYPAL_RETURN_URL", "http://localhost:8501")
# The only currency the agent will ever spend in. Offers in any other currency are refused.
CURRENCY = (_text("PAYPAL_CURRENCY", "USD") or "USD").upper()

# ---------- Safety rules (money in cents) ----------
SIGNING_SECRET = _text("SIGNING_SECRET")
APPROVAL_TTL_SECONDS = _int("APPROVAL_TTL_SECONDS", 300)
MAX_BUDGET_CENTS = _cents("MAX_BUDGET", 500)
STEP_UP_THRESHOLD_CENTS = _cents("STEP_UP_THRESHOLD", 25)
DAILY_CAP_CENTS = _cents("DAILY_CAP", 100)
MAX_ORDERS_PER_HOUR = _int("MAX_ORDERS_PER_HOUR", 3)

# ---------- Trusted merchants ----------
_default_trusted = "bestbuy.com,walmart.com,macys.com,target.com,demo-shop.example,budget-store.example"
TRUSTED_MERCHANTS = tuple(
    d.strip().lower() for d in _text("TRUSTED_MERCHANTS", _default_trusted).split(",") if d.strip()
)


def is_sandbox():
    return PAYPAL_ENV == "sandbox"


def signing_secret_ok():
    return len(SIGNING_SECRET) >= 32

# ---------- API access ----------
# Every /api route requires this key in the X-API-Key header.
API_KEY = _text("API_KEY")


def api_key_ok():
    return len(API_KEY) >= 32


# ---------- App ----------
_db = Path(_text("DB_PATH", "data/safecart.db"))
DB_PATH = _db if _db.is_absolute() else ROOT / _db
BACKEND_URL = _text("BACKEND_URL", "http://127.0.0.1:8000")


# These helpers read the values above at call time, so tests and a changed .env behave predictably.
def channel3_enabled():
    return bool(CHANNEL3_API_KEY)


def paypal_enabled():
    return bool(PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET)


def describe():
    """One safe line for status displays. Never includes a secret."""
    return (f"Channel3: {'on' if channel3_enabled() else 'off'} | "
            f"PayPal ({PAYPAL_ENV}): {'on' if paypal_enabled() else 'off'} | "
            f"Gemini: {'on' if GEMINI_API_KEY else 'off'} | "
            f"Demo catalog forced: {USE_DEMO_CATALOG}")
