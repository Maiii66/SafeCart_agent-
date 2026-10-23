# backend/services/channel3_service.py
# The ONLY file that talks to Channel3 (product search). Everything it returns is converted
# into our own immutable Product/Offer objects. Prices become integer cents immediately.

import re

import requests

from backend import config
from backend.models import Offer, Product
from backend.money import MoneyError, cents_to_float, price_to_cents

TIMEOUT = 20
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class Channel3Error(Exception):
    """Channel3 is switched off, unreachable, out of credits, or refused the request."""


def _headers():
    if not config.channel3_enabled():
        raise Channel3Error("Channel3 is not set up. Add CHANNEL3_API_KEY to .env.")
    return {"x-api-key": config.CHANNEL3_API_KEY, "Content-Type": "application/json"}


def _request(method, path, json=None):
    headers = _headers()
    try:
        response = requests.request(method, config.CHANNEL3_BASE_URL + path,
                                    headers=headers, json=json, timeout=TIMEOUT)
    except requests.exceptions.RequestException as error:
        raise Channel3Error(f"Could not reach Channel3: {error}") from error
    if response.status_code in (401, 403):
        raise Channel3Error("Channel3 rejected the API key. Check CHANNEL3_API_KEY.")
    if response.status_code == 429:
        raise Channel3Error("Channel3 rate limit or credits reached. Try again shortly.")
    if response.status_code >= 400:
        raise Channel3Error(f"Channel3 error {response.status_code}: {response.text[:200]}")
    try:
        return response.json()
    except ValueError as error:
        raise Channel3Error("Channel3 returned something that is not JSON.") from error


# ---------- parsing ----------

def _parse_offer(raw):
    try:
        price = raw.get("price") or {}
        return Offer(
            domain=str(raw.get("domain") or ""),
            price_cents=price_to_cents(price.get("price")),
            currency=str(price.get("currency") or ""),
            availability=str(raw.get("availability") or ""),
            condition=str(raw.get("condition") or ""),
            url=str(raw.get("url") or ""),
        )
    except (MoneyError, AttributeError, TypeError):
        return None


def _main_image(images):
    if not isinstance(images, list) or not images:
        return ""
    chosen = next((i for i in images if isinstance(i, dict) and i.get("is_main_image")),
                  images[0])
    if not isinstance(chosen, dict):
        return ""
    return str(chosen.get("cleaned_url") or chosen.get("url") or "")


def parse_product(raw):
    """Turn one raw Channel3 product into a Product, or None if it is unusable."""
    if not isinstance(raw, dict) or not raw.get("id") or not raw.get("title"):
        return None
    offers = tuple(o for o in (_parse_offer(r) for r in (raw.get("offers") or [])
                               if isinstance(r, dict)) if o is not None)
    brands = raw.get("brands") or []
    ratings = raw.get("ratings") or {}
    category = raw.get("category") or {}
    try:
        rating_value = float(ratings["value"]) if ratings.get("value") is not None else None
        rating_count = int(ratings.get("count") or 0)
    except (TypeError, ValueError, KeyError):
        rating_value, rating_count = None, 0
    return Product(
        id=str(raw["id"]),
        title=str(raw["title"]),
        description=str(raw.get("description") or ""),
        brand=str(brands[0].get("name", "")) if brands and isinstance(brands[0], dict) else "",
        image_url=_main_image(raw.get("images")),
        category=str(category.get("title") or "") if isinstance(category, dict) else "",
        rating_value=rating_value,
        rating_count=rating_count,
        key_features=tuple(str(f) for f in (raw.get("key_features") or [])),
        offers=offers,
    )


def parse_search_response(data):
    """Returns (products, skipped_count). Malformed products are skipped, never trusted."""
    raw_products = data.get("products") if isinstance(data, dict) else None
    if not isinstance(raw_products, list):
        return [], 0
    products = [p for p in (parse_product(r) for r in raw_products) if p is not None]
    return products, len(raw_products) - len(products)


# ---------- calls ----------

def search(query, max_price_cents=None, limit=10):
    """Search Channel3. The price filter narrows results; guardrails still re-check every price."""
    filters = {"availability": ["InStock"]}
    if max_price_cents is not None:
        filters["price"] = {"min_price": 0, "max_price": cents_to_float(max_price_cents)}
    data = _request("POST", "/v1/search",
                    json={"query": str(query)[:200], "limit": int(limit), "filters": filters})
    products, _skipped = parse_search_response(data)
    return products


def get_product(product_id):
    """Fetch one product's CURRENT details (used to re-check the live price before paying)."""
    if not isinstance(product_id, str) or not _ID.match(product_id):
        raise Channel3Error("That is not a valid product id.")
    data = _request("GET", f"/v1/products/{product_id}")
    product = parse_product(data.get("product", data) if isinstance(data, dict) else None)
    if product is None:
        raise Channel3Error("Channel3 returned a product we could not read.")
    return product
