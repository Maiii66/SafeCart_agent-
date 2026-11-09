from urllib.parse import urlparse

from backend.money import format_usd


def safe_usd(cents) -> str:
    """Format a cent value for display without exposing conversion errors."""
    try:
        return format_usd(int(cents))
    except (TypeError, ValueError):
        return "$0.00"


def safe_http_url(url) -> bool:
    """Return whether a value is an absolute HTTP(S) URL."""
    if not isinstance(url, str):
        return False
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)
