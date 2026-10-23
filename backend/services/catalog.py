# backend/services/catalog.py
# One door for "find products" and "what does this cost RIGHT NOW".
#
# Search is forgiving: if Channel3 is down or out of credits it falls back to the demo catalog
# so the app keeps working. Live-price checks are NOT forgiving: they fail closed, because a
# payment must never go ahead on a price nobody could verify.

from dataclasses import dataclass

from backend import config
from backend.services import catalog_demo, channel3_service

SOURCE_CHANNEL3 = "channel3"
SOURCE_DEMO = "demo"


class LivePriceUnavailable(Exception):
    """The current price could not be confirmed, so no payment may go ahead."""


@dataclass(frozen=True)
class CatalogResult:
    products: tuple
    source: str
    note: str = ""


def search_products(query, max_price_cents=None, limit=10):
    if config.USE_DEMO_CATALOG:
        return CatalogResult(tuple(catalog_demo.search(query, max_price_cents, limit)), SOURCE_DEMO,
                             "Demo catalog (USE_DEMO_CATALOG is on).")
    if not config.channel3_enabled():
        return CatalogResult(tuple(catalog_demo.search(query, max_price_cents, limit)), SOURCE_DEMO,
                             "No Channel3 key set, so the demo catalog was used.")
    try:
        products = channel3_service.search(query, max_price_cents, limit)
        return CatalogResult(tuple(products), SOURCE_CHANNEL3)
    except channel3_service.Channel3Error as error:
        if not config.CATALOG_FALLBACK:
            raise
        return CatalogResult(tuple(catalog_demo.search(query, max_price_cents, limit)), SOURCE_DEMO,
                             f"Channel3 was unavailable ({error}), so the demo catalog was used.")


def get_live_product(product_id, source):
    """Fetch the current listing from the SAME source the person approved it from."""
    if source == SOURCE_DEMO:
        product = catalog_demo.get_product(product_id)
        if product is None:
            raise LivePriceUnavailable("That product is no longer in the demo catalog.")
        return product
    if source == SOURCE_CHANNEL3:
        try:
            return channel3_service.get_product(product_id)
        except channel3_service.Channel3Error as error:
            raise LivePriceUnavailable(f"Could not confirm the current price: {error}") from error
    raise LivePriceUnavailable(f"Unknown product source '{source}'.")
