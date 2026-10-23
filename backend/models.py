# backend/models.py
# Plain, immutable data shapes shared by every module.

from dataclasses import dataclass


@dataclass(frozen=True)
class Offer:
    """One retailer's listing for a product. The price is in integer cents."""
    domain: str
    price_cents: int
    currency: str
    availability: str
    condition: str
    url: str


@dataclass(frozen=True)
class Product:
    """A product as returned by search. Text fields (title, description, key_features) come
    from the internet and are UNTRUSTED: they are never obeyed, only displayed and scanned."""
    id: str
    title: str
    description: str
    brand: str
    image_url: str
    category: str
    rating_value: float | None
    rating_count: int
    key_features: tuple
    offers: tuple


@dataclass(frozen=True)
class Candidate:
    """A product paired with the single offer the agent would buy."""
    product: Product
    offer: Offer
