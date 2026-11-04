from __future__ import annotations

import html
import io
import sys
import wave
from pathlib import Path
from urllib.parse import urlparse

import requests
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import config
from backend.agent import parser, voice
from backend.money import format_usd

st.set_page_config(
    page_title="SafeCart",
    page_icon=":material/shopping_bag:",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DEMO_QUERY = "find me wireless earbuds under fifty dollars"
DEMO_ITEM = "earbuds"
DEMO_BUDGET_CENTS = 5000
API_TIMEOUT_SECONDS = 10

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700&family=Newsreader:opsz,wght@6..72,400;6..72,500;6..72,600;6..72,700&display=swap');

html, body, .stApp, [data-testid="stAppViewContainer"] {
    background: #FFF3D5 !important;
    color: #2D3B2D;
    font-family: "Manrope", sans-serif;
}

[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"],
#MainMenu, footer {
    display: none !important;
}

.block-container {
    max-width: 1120px;
    padding-top: 1.25rem;
    padding-bottom: 4rem;
}

.stApp, .stMarkdown, p, label, input, button, a, li {
    font-family: "Manrope", sans-serif;
}

h1, h2, h3, h4, h5, h6,
.sc-logo, .sc-hero h1, .sc-readback-text, .sc-results-head h2, .sc-empty,
.sc-page-title {
    font-family: "Newsreader", Georgia, serif !important;
    color: #1B3022 !important;
}

.st-key-safecart_header {
    border-bottom: 1px solid #E8DEC5;
    padding: 0.35rem 0 1rem 0;
}

.sc-logo {
    font-size: 1.9rem;
    font-weight: 700;
    letter-spacing: -0.03em;
    line-height: 1;
}

.sc-nav-row {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    align-items: center;
    gap: 0.8rem;
}

.st-key-nav_tabs button,
.st-key-pause_toggle button {
    border-radius: 9999px !important;
    font-family: "Manrope", sans-serif !important;
    font-weight: 700 !important;
    min-height: 2.35rem;
    box-shadow: none !important;
}

.st-key-nav_tabs button[kind="secondary"] {
    border: 1px solid #E8DEC5 !important;
    background: transparent !important;
    color: #2D3B2D !important;
}

.st-key-nav_tabs button[kind="primary"] {
    border: 1px solid #1B3022 !important;
    background: #1B3022 !important;
    color: #FFF3D5 !important;
}

.st-key-pause_toggle button {
    border: 1px solid #1B3022 !important;
    background: #FCF8EE !important;
    color: #1B3022 !important;
}

.st-key-pause_toggle button:hover {
    background: #FFFFFF !important;
}

.sc-hero {
    padding: 4.5rem 0 2.4rem 0;
    text-align: center;
}

.sc-hero h1 {
    font-size: clamp(2.5rem, 5.2vw, 4.15rem);
    font-weight: 500;
    line-height: 1.04;
    letter-spacing: -0.035em;
    margin: 0 auto;
    max-width: 900px;
}

[data-testid="stForm"] {
    background: #FFFFFF !important;
    border: 1px solid #E8DEC5 !important;
    border-radius: 9999px !important;
    padding: 0.4rem 0.5rem 0.4rem 1.35rem !important;
    max-width: 860px;
    margin: 0 auto;
    box-shadow: 0 16px 40px rgba(27, 48, 34, 0.06);
}

[data-testid="stForm"] [data-testid="stFormSubmitButton"] {
    margin: 0;
}

[data-testid="stForm"] [data-baseweb="input"],
[data-testid="stForm"] [data-baseweb="base-input"],
[data-testid="stForm"] [data-testid="stTextInput"] > div {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
}

[data-testid="stForm"] input,
[data-testid="stTextInput"] input {
    background: #FFFFFF !important;
    color: #1B3022 !important;
    font-family: "Manrope", sans-serif !important;
    font-size: 1rem !important;
    padding: 0.55rem 0.8rem !important;
    border: 1px solid #E8DEC5 !important;
    border-radius: 4px !important;
}

[data-testid="stForm"] input {
    background: transparent !important;
    border: none !important;
    padding: 0.55rem 0 !important;
    border-radius: 0 !important;
}

[data-testid="stForm"] input::placeholder {
    color: #7C8578 !important;
    opacity: 1;
}

.st-key-search_submit button {
    height: 2.75rem !important;
    padding: 0 1.6rem !important;
    border-radius: 9999px !important;
    background: #1B3022 !important;
    color: #FFF3D5 !important;
    border: 1px solid #1B3022 !important;
    font-weight: 700 !important;
}

.st-key-search_submit button:hover {
    background: #2D3B2D !important;
    border-color: #2D3B2D !important;
}

div[data-testid="stVerticalBlockBorderWrapper"] {
    background: #FFFFFF !important;
    border: 1px solid #E8DEC5 !important;
    border-radius: 8px !important;
    padding: 20px 22px !important;
    box-shadow: 0 14px 34px rgba(27, 48, 34, 0.05);
}

.st-key-readback_card,
.st-key-voice_preview_card {
    padding: 24px !important;
}

.sc-readback {
    display: flex;
    gap: 0.75rem;
    align-items: flex-start;
    margin: 0.15rem 0 1.15rem 0;
}

.sc-ai-label {
    background: #1B3022;
    color: #FFF3D5;
    border-radius: 9999px;
    padding: 0.22rem 0.6rem;
    font-family: "Manrope", sans-serif;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.14em;
    line-height: 1.2;
    flex: 0 0 auto;
}

.sc-readback-text {
    font-family: "Newsreader", Georgia, serif;
    font-size: 1.35rem;
    line-height: 1.5;
    color: #1B3022;
    font-weight: 500;
}

.sc-readback mark {
    background: #FFF3D5;
    color: #1B3022;
    border: 1px solid #E8DEC5;
    border-radius: 9999px;
    padding: 0.03em 0.42em;
    font-weight: 600;
}

.stButton > button {
    border-radius: 9999px !important;
    font-family: "Manrope", sans-serif !important;
    font-weight: 700 !important;
    border: 1px solid #E8DEC5 !important;
    min-height: 2.6rem;
    box-shadow: none !important;
}

.stButton > button[kind="primary"] {
    background: #1B3022 !important;
    color: #FFF3D5 !important;
    border-color: #1B3022 !important;
}

.stButton > button[kind="primary"]:hover {
    background: #2D3B2D !important;
    border-color: #2D3B2D !important;
}

.stButton > button[kind="secondary"] {
    background: transparent !important;
    color: #1B3022 !important;
    border-color: #E8DEC5 !important;
}

.stButton > button[kind="secondary"]:hover {
    background: #FCF8EE !important;
    border-color: #1B3022 !important;
}

.sc-notice,
.sc-error {
    max-width: 860px;
    margin: 0.9rem auto 0 auto;
    padding: 0.7rem 1rem;
    border: 1px solid #E8DEC5;
    border-radius: 8px;
    background: #FCF8EE;
    color: #2D3B2D;
    font-size: 0.86rem;
    line-height: 1.45;
}

.sc-error {
    background: #FFFFFF;
    color: #1B3022;
    font-weight: 600;
}

.sc-results-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 1rem;
    margin: 3rem 0 1rem 0;
    border-top: 1px solid #E8DEC5;
    padding-top: 1.6rem;
}

.sc-results-head h2 {
    font-size: 2rem;
    font-weight: 500;
    margin: 0;
}

.sc-results-meta {
    color: #2D3B2D;
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
}

.sc-product {
    min-height: 170px;
}

.sc-product-top {
    display: flex;
    justify-content: space-between;
    gap: 1rem;
    align-items: center;
}

.sc-product-domain {
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #2D3B2D;
    background: #FCF8EE;
    border: 1px solid #E8DEC5;
    border-radius: 9999px;
    padding: 0.18rem 0.55rem;
}

.sc-product-price {
    font-family: "Newsreader", Georgia, serif;
    font-size: 1.45rem;
    font-weight: 600;
    color: #1B3022;
}

.sc-product h3 {
    font-size: 1.25rem;
    line-height: 1.3;
    margin: 0.85rem 0 0.35rem 0;
    font-weight: 600;
}

.sc-product-meta {
    font-size: 0.8rem;
    color: #2D3B2D;
    margin-bottom: 0.7rem;
}

.sc-product-foot {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
    margin-top: 1rem;
}

.sc-score {
    font-size: 0.75rem;
    font-weight: 700;
    color: #1B3022;
    background: #FFF3D5;
    border-radius: 9999px;
    padding: 0.2rem 0.6rem;
    border: 1px solid #E8DEC5;
}

.sc-tag {
    font-size: 0.72rem;
    font-weight: 700;
    color: #1B3022;
    border: 1px solid #E8DEC5;
    background: #FCF8EE;
    border-radius: 9999px;
    padding: 0.2rem 0.6rem;
    text-transform: capitalize;
}

.sc-listing-link {
    margin-left: auto;
    color: #1B3022 !important;
    font-weight: 700;
    font-size: 0.82rem;
    text-decoration: none;
    border-bottom: 1px solid #1B3022;
}

.sc-empty {
    padding: 1.4rem 1.6rem;
    border: 1px solid #E8DEC5;
    border-radius: 8px;
    background: #FFFFFF;
    color: #2D3B2D;
    font-family: "Newsreader", Georgia, serif;
    font-size: 1.2rem;
}

.sc-page-title {
    font-size: clamp(2rem, 4vw, 3.2rem);
    font-weight: 500;
    margin: 2.2rem 0 1.2rem 0;
}

.sc-voice-wrap {
    max-width: 860px;
    margin: 0 auto;
}

[data-testid="stAudioInput"] {
    background: #FFFFFF !important;
    border: 1px solid #E8DEC5 !important;
    border-radius: 8px !important;
    padding: 1rem !important;
}

@media (max-width: 760px) {
    .block-container {
        padding-left: 1rem;
        padding-right: 1rem;
    }

    .sc-hero {
        padding-top: 2.6rem;
    }

    [data-testid="stForm"] {
        border-radius: 18px !important;
        padding: 0.6rem 0.7rem !important;
    }

    .sc-readback-text {
        font-size: 1.15rem;
    }
}
"""

st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)


def _headers() -> dict:
    return {"X-API-Key": config.API_KEY, "Content-Type": "application/json"}


def _api(method: str, path: str, payload: dict | None = None):
    base = config.BACKEND_URL.rstrip("/")
    try:
        response = requests.request(
            method,
            f"{base}{path}",
            headers=_headers(),
            json=payload,
            timeout=API_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return None, (
            "Cannot reach the SafeCart backend. Start it with: "
            "uvicorn backend.main:app --reload"
        )

    if response.status_code == 401:
        return None, "The backend rejected the API key. Check the server configuration."
    if response.status_code == 503:
        return None, "The backend API key is not configured on the server."

    try:
        data = response.json()
    except ValueError:
        data = None

    if response.status_code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else None
        return None, str(detail or f"Request failed ({response.status_code}).")

    return data, ""


def _refresh_status() -> None:
    data, error = _api("GET", "/api/status")
    if isinstance(data, dict):
        st.session_state.status = data
    else:
        st.session_state.status_error = error


def _safe_usd(cents) -> str:
    try:
        return format_usd(int(cents))
    except (TypeError, ValueError):
        return "$0.00"


def _init_state() -> None:
    defaults = {
        "page": "Shop",
        "readback": None,
        "pending_query": "",
        "pending_budget_cents": None,
        "shop_response": None,
        "search_error": "",
        "status": {},
        "status_error": "",
        "notice": "",
        "cart": [],
        "voice_result": None,
        "voice_clip_bytes": None,
        "voice_mime": "audio/wav",
        "pending_search_from_voice": False,
        "checkout_status": "",
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)

    if not st.session_state.get("_demo_ready"):
        st.session_state._demo_ready = True
        st.session_state.readback = {
            "item": DEMO_ITEM,
            "budget_cents": DEMO_BUDGET_CENTS,
        }
        st.session_state.pending_query = DEMO_QUERY
        st.session_state.pending_budget_cents = DEMO_BUDGET_CENTS


def _toggle_pause(pause: bool) -> None:
    endpoint = "/api/pause" if pause else "/api/resume"
    data, error = _api("POST", endpoint, {})
    if isinstance(data, dict):
        st.session_state.status = data
        st.session_state.notice = (
            "SafeCart is paused. No new spending can start."
            if data.get("paused")
            else "SafeCart is active again."
        )
    else:
        st.session_state.notice = error


def _render_header() -> None:
    paused = bool(st.session_state.status.get("paused"))
    cart_count = len(st.session_state.cart)
    with st.container(key="safecart_header"):
        logo_col, nav_col, pause_col = st.columns(
            [2.0, 6.0, 1.4], vertical_alignment="center"
        )
        with logo_col:
            st.markdown('<div class="sc-logo">SafeCart</div>', unsafe_allow_html=True)
        with nav_col:
            st.markdown('<div class="sc-nav-row">', unsafe_allow_html=True)
            tabs = ["Shop", "Voice", "Activity", "Log", f"Cart ({cart_count})"]
            with st.container(key="nav_tabs"):
                cols = st.columns(len(tabs))
                for idx, tab in enumerate(tabs):
                    is_active = (st.session_state.page == "Shop" and tab == "Shop") or (
                        st.session_state.page == "Voice" and tab == "Voice"
                    ) or (st.session_state.page == "Activity" and tab == "Activity") or (
                        st.session_state.page == "Log" and tab == "Log"
                    ) or (st.session_state.page == "Cart" and tab.startswith("Cart"))
                    with cols[idx]:
                        if st.button(
                            tab,
                            key=f"nav_{tab}",
                            type="primary" if is_active else "secondary",
                            use_container_width=True,
                        ):
                            if tab == "Shop":
                                st.session_state.page = "Shop"
                            elif tab == "Voice":
                                st.session_state.page = "Voice"
                            elif tab == "Activity":
                                st.session_state.page = "Activity"
                            elif tab == "Log":
                                st.session_state.page = "Log"
                            elif tab.startswith("Cart"):
                                st.session_state.page = "Cart"
                            st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)
        with pause_col:
            if st.button(
                "Resume" if paused else "Pause",
                key="pause_toggle",
                use_container_width=True,
                disabled=not bool(st.session_state.status),
                help="Pause all spending instantly",
            ):
                _toggle_pause(not paused)
                st.rerun()


def _handle_search(text: str) -> None:
    text = text.strip()
    if not text:
        st.session_state.notice = "Tell me what you want and your maximum budget."
        return

    try:
        parsed = parser.parse_request(text)
    except parser.ParseError as error:
        st.session_state.notice = str(error)
        return

    st.session_state.shop_response = None
    st.session_state.search_error = ""
    st.session_state.page = "Shop"

    if not parsed.items:
        message = parser.readback_text(parsed)
        st.session_state.readback = {"item": None, "budget_cents": None}
        st.session_state.pending_query = ""
        st.session_state.pending_budget_cents = None
        st.session_state.notice = message
        return

    item = parsed.items[0].query
    budget = parsed.items[0].budget_cents
    if budget is None:
        budget = parsed.total_budget_cents

    st.session_state.readback = {"item": item, "budget_cents": budget}
    st.session_state.pending_query = item
    st.session_state.pending_budget_cents = budget

    if budget is None:
        st.session_state.notice = "Add a maximum budget, then search again."
    else:
        st.session_state.notice = "Review the read-back and confirm to search."


def _run_search() -> None:
    query = st.session_state.get("pending_query") or ""
    budget = st.session_state.get("pending_budget_cents")
    if not query or budget is None:
        return

    data, error = _api(
        "POST",
        "/api/shop",
        {"query": query, "max_price_cents": budget, "limit": 10},
    )
    if isinstance(data, dict):
        st.session_state.shop_response = data
        st.session_state.search_error = ""
    else:
        st.session_state.shop_response = None
        st.session_state.search_error = error or "Product search failed."


def _edit_request() -> None:
    st.session_state.readback = None
    st.session_state.pending_query = ""
    st.session_state.pending_budget_cents = None
    st.session_state.shop_response = None
    st.session_state.search_error = ""
    st.session_state.page = "Shop"
    st.session_state.notice = "Update your request, then press Search."


def _render_search() -> None:
    with st.form("search_form", clear_on_submit=False):
        text_col, submit_col = st.columns([9, 1.6], vertical_alignment="center")
        with text_col:
            query = st.text_input(
                "Search request",
                placeholder="find me wireless earbuds under fifty dollars",
                label_visibility="collapsed",
                key="search_input",
            )
        with submit_col:
            submitted = st.form_submit_button(
                "Search",
                type="primary",
                use_container_width=True,
                key="search_submit",
            )

    if submitted:
        _handle_search(query)


def _render_notice() -> None:
    message = st.session_state.get("notice")
    if message:
        st.markdown(
            f'<div class="sc-notice">{html.escape(str(message))}</div>',
            unsafe_allow_html=True,
        )
        st.session_state.notice = ""


def _render_readback() -> None:
    if st.session_state.page != "Shop":
        return
    readback = st.session_state.get("readback")
    if not isinstance(readback, dict):
        return

    item = readback.get("item")
    budget = readback.get("budget_cents")

    if item and budget is not None:
        body = (
            f"(heard: <mark>{html.escape(str(item))}</mark>, "
            f"<mark>maximum {html.escape(_safe_usd(budget))}</mark>. "
            "Is that right? I will search Channel3 for live listings.)"
        )
    elif item:
        body = (
            f"(heard: <mark>{html.escape(str(item))}</mark>. "
            "I could not find a budget. Please add one before I search.)"
        )
    else:
        body = "(I could not tell what you want to buy. Please try another request.)"

    with st.container(border=True, key="readback_card"):
        st.markdown(
            '<div class="sc-readback">'
            '<span class="sc-ai-label">AI</span>'
            f'<span class="sc-readback-text">{body}</span>'
            "</div>",
            unsafe_allow_html=True,
        )

        confirm_col, edit_col, _ = st.columns([1.7, 1, 2.4], vertical_alignment="center")
        with confirm_col:
            can_search = bool(st.session_state.pending_query) and (
                st.session_state.pending_budget_cents is not None
            )
            if st.button(
                "Confirm & Search",
                type="primary",
                use_container_width=True,
                disabled=not can_search,
                key="confirm_search",
            ):
                _run_search()
        with edit_col:
            if st.button(
                "Edit",
                type="secondary",
                use_container_width=True,
                key="edit_search",
            ):
                _edit_request()
                st.rerun()


def _add_to_cart(product: dict) -> None:
    ranking = product.get("ranking") if isinstance(product.get("ranking"), dict) else {}
    offer = ranking.get("offer") if isinstance(ranking.get("offer"), dict) else {}
    product_id = str(product.get("id") or "")
    domain = str(offer.get("domain") or "")
    title = str(product.get("title") or "Untitled listing")
    price_cents = offer.get("price_cents")
    url = str(offer.get("url") or "")

    for item in st.session_state.cart:
        if item.get("product_id") == product_id and item.get("domain") == domain:
            st.session_state.notice = "This item is already in your cart."
            return

    st.session_state.cart.append(
        {
            "product_id": product_id,
            "title": title,
            "domain": domain,
            "price_cents": price_cents,
            "url": url,
        }
    )
    st.session_state.notice = "Added to cart."


def _product_card(product: dict, show_cart_button: bool = True) -> None:
    ranking = product.get("ranking") if isinstance(product.get("ranking"), dict) else {}
    offer = ranking.get("offer") if isinstance(ranking.get("offer"), dict) else {}

    title = html.escape(str(product.get("title") or "Untitled listing"))
    domain = html.escape(str(offer.get("domain") or "Unknown merchant"))
    price = html.escape(_safe_usd(offer.get("price_cents")))
    brand = html.escape(str(product.get("brand") or ""))
    rating = product.get("rating_value")
    rating_count = product.get("rating_count")
    score = ranking.get("score")
    flags = ranking.get("flags") if isinstance(ranking.get("flags"), list) else []

    meta_parts = []
    if brand:
        meta_parts.append(brand)
    if isinstance(rating, (int, float)) and not isinstance(rating, bool):
        count_text = (
            f" ({int(rating_count):,} reviews)"
            if isinstance(rating_count, int) and not isinstance(rating_count, bool)
            else ""
        )
        meta_parts.append(f"{rating:.1f}{count_text}")

    meta_html = " &middot; ".join(meta_parts)
    score_html = (
        f'<span class="sc-score">Match {score:.1f}</span>'
        if isinstance(score, (int, float)) and not isinstance(score, bool)
        else ""
    )
    flags_html = "".join(
        f'<span class="sc-tag">{html.escape(str(flag).replace("_", " "))}</span>'
        for flag in flags
    )

    url = str(offer.get("url") or "")
    parsed_url = urlparse(url)
    link_html = ""
    if parsed_url.scheme in ("http", "https") and parsed_url.netloc:
        safe_url = html.escape(url, quote=True)
        link_html = (
            f'<a class="sc-listing-link" href="{safe_url}" '
            'target="_blank" rel="noopener noreferrer">View listing</a>'
        )

    st.markdown(
        '<div class="sc-product">'
        '<div class="sc-product-top">'
        f'<span class="sc-product-domain">{domain}</span>'
        f'<span class="sc-product-price">{price}</span>'
        "</div>"
        f"<h3>{title}</h3>"
        f'<div class="sc-product-meta">{meta_html}</div>'
        '<div class="sc-product-foot">'
        f"{score_html}{flags_html}{link_html}"
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    if show_cart_button:
        if st.button("Add to Cart 🛒", key=f"add_cart_{product.get('id')}_{domain}", type="secondary"):
            _add_to_cart(product)
            st.rerun()


def _render_results() -> None:
    if st.session_state.page != "Shop":
        return
    response = st.session_state.get("shop_response")
    if not isinstance(response, dict):
        return

    products = response.get("products")
    products = products if isinstance(products, list) else []
    source = html.escape(str(response.get("source") or "catalog"))
    meta = f"{len(products)} ranked &middot; {source}"

    st.markdown(
        '<div class="sc-results-head">'
        "<h2>Live listings</h2>"
        f'<span class="sc-results-meta">{meta}</span>'
        "</div>",
        unsafe_allow_html=True,
    )

    if not products:
        st.markdown(
            '<div class="sc-empty">No listings matched that request and budget. '
            "Try a different item or a higher maximum.</div>",
            unsafe_allow_html=True,
        )
    else:
        for product in products[:10]:
            if not isinstance(product, dict):
                continue
            with st.container(border=True):
                _product_card(product, show_cart_button=True)

    blocked = response.get("blocked")
    blocked = blocked if isinstance(blocked, list) else []
    rejected = response.get("rejected")
    rejected = rejected if isinstance(rejected, list) else []
    duplicates = response.get("duplicates")

    if blocked or rejected:
        with st.expander("Safety filters", expanded=False):
            if blocked:
                st.markdown("**Blocked by the injection guard**")
                for item in blocked:
                    if isinstance(item, dict):
                        title = item.get("title") or "Untitled listing"
                        reasons = ", ".join(str(r) for r in item.get("reasons") or [])
                        st.write(f"{title} - {reasons}")
            if rejected:
                st.markdown("**Removed before ranking**")
                for item in rejected:
                    if isinstance(item, dict):
                        title = item.get("title") or "Untitled listing"
                        reason = item.get("reason") or item.get("code") or ""
                        st.write(f"{title} - {reason}")

    if isinstance(duplicates, int) and duplicates > 0:
        st.caption(f"{duplicates} duplicate listing(s) merged.")

    note = response.get("note")
    if note:
        st.caption(str(note))


def _render_search_error() -> None:
    error = st.session_state.get("search_error")
    if error:
        st.markdown(
            f'<div class="sc-error">{html.escape(str(error))}</div>',
            unsafe_allow_html=True,
        )


def _render_voice_page() -> None:
    if st.session_state.page != "Voice":
        return
    st.markdown('<h1 class="sc-page-title">Voice</h1>', unsafe_allow_html=True)
    st.markdown('<div class="sc-voice-wrap">', unsafe_allow_html=True)
    st.markdown(
        '<p style="color:#2D3B2D; margin-bottom:1rem;">Record your request with your microphone. '
        "Max 15 seconds. Audio is transcribed and not stored.</p>",
        unsafe_allow_html=True,
    )

    audio = st.audio_input("Record your request (max 15s)", key="voice_audio_input")

    if audio is not None:
        try:
            audio_bytes = audio.getvalue()
            st.session_state.voice_clip_bytes = audio_bytes
            st.session_state.voice_mime = audio.type or "audio/wav"
        except Exception:
            st.session_state.notice = "Could not read the recorded audio."

        clip = voice.AudioClip(data=st.session_state.voice_clip_bytes or b"", mime_type=st.session_state.voice_mime)
        # Try to transcribe if possible; if no transcriber, backend rules may refuse
        result = voice.transcribe_clip(clip, transcribe=None)
        st.session_state.voice_result = result

        if result.ok and result.action == "search" and result.parsed is not None:
            with st.container(border=True, key="voice_preview_card"):
                st.markdown(
                    '<div class="sc-readback">'
                    '<span class="sc-ai-label">AI</span>'
                    f'<span class="sc-readback-text">{html.escape(result.readback)}</span>'
                    "</div>",
                    unsafe_allow_html=True,
                )
            if st.button("Confirm & Search", type="primary", key="voice_confirm_search"):
                try:
                    parsed = result.parsed
                    item = parsed.items[0].query if parsed.items else ""
                    budget = parsed.items[0].budget_cents if parsed.items else None
                    if budget is None and hasattr(parsed, "total_budget_cents"):
                        budget = parsed.total_budget_cents
                    if item and budget is not None:
                        st.session_state.readback = {"item": item, "budget_cents": budget}
                        st.session_state.pending_query = item
                        st.session_state.pending_budget_cents = budget
                        st.session_state.shop_response = None
                        st.session_state.search_error = ""
                        st.session_state.page = "Shop"
                        _run_search()
                        st.rerun()
                    else:
                        st.session_state.notice = result.readback
                except Exception:
                    st.session_state.notice = "Could not process the voice request."
        else:
            st.markdown(
                f'<div class="sc-error">{html.escape(result.reason or result.readback or "Voice request could not be processed.")}</div>',
                unsafe_allow_html=True,
            )
            if result.code == "TRANSCRIPTION_UNAVAILABLE":
                st.markdown(
                    '<div class="sc-notice">Transcription is not configured in this environment. '
                    'Type your request in the Shop tab instead.</div>',
                    unsafe_allow_html=True,
                )

    st.markdown("</div>", unsafe_allow_html=True)


def _render_cart_page() -> None:
    if st.session_state.page != "Cart":
        return
    st.markdown('<h1 class="sc-page-title">Cart</h1>', unsafe_allow_html=True)
    cart = st.session_state.cart
    if not cart:
        st.markdown(
            '<div class="sc-empty">Your cart is empty. Add items from the Shop tab.</div>',
            unsafe_allow_html=True,
        )
        return

    total_cents = 0
    for idx, item in enumerate(cart):
        price_cents = item.get("price_cents")
        try:
            if isinstance(price_cents, int) and not isinstance(price_cents, bool):
                total_cents += price_cents
        except Exception:
            pass
        title = html.escape(str(item.get("title") or "Untitled listing"))
        domain = html.escape(str(item.get("domain") or ""))
        price = html.escape(_safe_usd(price_cents))
        url = str(item.get("url") or "")
        parsed_url = urlparse(url)
        link_html = ""
        if parsed_url.scheme in ("http", "https") and parsed_url.netloc:
            safe_url = html.escape(url, quote=True)
            link_html = (
                f' <a class="sc-listing-link" href="{safe_url}" '
                'target="_blank" rel="noopener noreferrer">View listing</a>'
            )
        with st.container(border=True):
            st.markdown(
                '<div class="sc-product">'
                '<div class="sc-product-top">'
                f'<span class="sc-product-domain">{domain}</span>'
                f'<span class="sc-product-price">{price}</span>'
                "</div>"
                f"<h3>{title}</h3>"
                f'<div class="sc-product-foot">{link_html}</div>'
                "</div>",
                unsafe_allow_html=True,
            )
            if st.button("Remove", key=f"remove_cart_{idx}", type="secondary"):
                st.session_state.cart.pop(idx)
                st.rerun()

    st.markdown(
        f'<div style="margin-top:1.2rem; display:flex; justify-content:space-between; align-items:center; padding:1rem 1.2rem; border:1px solid #E8DEC5; border-radius:8px; background:#FFFFFF;">'
        f'<span style="font-family:Newsreader,serif; font-size:1.4rem; color:#1B3022;">Total</span>'
        f'<span style="font-family:Newsreader,serif; font-size:1.6rem; color:#1B3022;">{html.escape(_safe_usd(total_cents))}</span>'
        "</div>",
        unsafe_allow_html=True,
    )

    if st.button("Checkout (PayPal Sandbox)", type="primary", key="checkout_cart"):
        st.session_state.checkout_status = "Simulated PayPal Sandbox checkout completed. No payment was processed."
        st.session_state.cart = []
        st.rerun()

    if st.session_state.checkout_status:
        st.markdown(
            f'<div class="sc-notice">{html.escape(st.session_state.checkout_status)}</div>',
            unsafe_allow_html=True,
        )
        st.session_state.checkout_status = ""


def _render_activity_log() -> None:
    if st.session_state.page not in ("Activity", "Log"):
        return
    st.markdown(f'<h1 class="sc-page-title">{st.session_state.page}</h1>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sc-empty">Activity and logs are available via the backend API. '
        "Use the API routes /api/logs and /api/orders for full details.</div>",
        unsafe_allow_html=True,
    )


_init_state()
_refresh_status()
_render_header()

if st.session_state.page == "Shop":
    st.markdown(
        '<section class="sc-hero" id="shop">'
        "<h1>What would you like to buy today?</h1>"
        "</section>",
        unsafe_allow_html=True,
    )
    st.markdown('<div id="shop_search"></div>', unsafe_allow_html=True)
    _render_search()
    _render_notice()
    _render_readback()
    _render_search_error()
    _render_results()
elif st.session_state.page == "Voice":
    _render_notice()
    _render_voice_page()
elif st.session_state.page == "Cart":
    _render_notice()
    _render_cart_page()
else:
    _render_notice()
    _render_activity_log()
