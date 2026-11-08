from __future__ import annotations

import html
import io
import sys
import wave
from pathlib import Path
from urllib.parse import urlparse

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import config
from backend.agent import parser, understand, voice
from backend.money import MoneyError, format_usd, parse_dollars_to_cents
from backend.services import llm
from frontend import checkout_flow as flow
from frontend import client as api_client

st.set_page_config(
    page_title="SafeCart",
    page_icon=":material/shopping_bag:",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DEMO_QUERY = "find me wireless earbuds under fifty dollars"
DEMO_ITEM = "earbuds"
DEMO_BUDGET_CENTS = 5000
RESET_APPROVAL_CODES = {
    "ALREADY_USED",
    "BAD_PAYLOAD",
    "BAD_SIGNATURE",
    "EXPIRED",
    "NO_SUCH_APPROVAL",
    "OVER_BUDGET",
    "PAYPAL_FAILED",
    "PRICE_CHANGED",
}

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700&family=Newsreader:opsz,wght@6..72,400;6..72,500;6..72,600;6..72,700&display=swap');

html, body, .stApp, [data-testid="stAppViewContainer"] {
    background: #FFF3D5 !important;
    color: #2D3B2D;
    font-family: "Manrope", sans-serif;
}

[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"],
#MainMenu, footer, a[href="#main-content"], [data-testid="stStatusWidget"] {
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
    padding: 0.35rem 0.5rem 0.35rem 1.4rem !important;
    max-width: 860px;
    margin: 0 auto;
    box-shadow: 0 16px 40px rgba(27, 48, 34, 0.06);
    overflow: hidden !important;
}

[data-testid="stForm"] [data-testid="stFormSubmitButton"] {
    margin: 0 !important;
}

[data-testid="stForm"] [data-testid="stVerticalBlockBorderWrapper"],
[data-testid="stForm"] [data-testid="stVerticalBlock"],
[data-testid="stForm"] [data-testid="stLayoutWrapper"],
[data-testid="stForm"] [data-testid="stHorizontalBlock"] {
    border: none !important;
    background: transparent !important;
    box-shadow: none !important;
    padding: 0 !important;
    gap: 0 !important;
}

[data-testid="stForm"] [data-testid="stTextInputRootElement"],
[data-testid="stForm"] .react-aria-TextField,
[data-testid="stForm"] [data-baseweb="input"],
[data-testid="stForm"] [data-baseweb="base-input"],
[data-testid="stForm"] [data-testid="stTextInput"] div {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    outline: none !important;
    padding: 0 !important;
}

[data-testid="stForm"] input[data-testid="stTextInputField"],
[data-testid="stForm"] input {
    background: transparent !important;
    border: none !important;
    padding: 0.65rem 0 !important;
    border-radius: 0 !important;
    font-family: "Manrope", sans-serif !important;
    font-size: 1.05rem !important;
    color: #1B3022 !important;
    outline: none !important;
    box-shadow: none !important;
}

[data-testid="stForm"] input::placeholder {
    color: #7C8578 !important;
    opacity: 0.95;
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

/* Fix regular inputs across the app (cart budget, step-up confirmation) */
div:not([data-testid="stForm"]) [data-testid="stTextInputRootElement"] {
    background: #FFFFFF !important;
    border: 1px solid #E8DEC5 !important;
    border-radius: 8px !important;
    padding: 0.15rem 0.5rem !important;
    box-shadow: none !important;
}

div:not([data-testid="stForm"]) [data-testid="stTextInputRootElement"]:focus-within {
    border-color: #1B3022 !important;
    box-shadow: 0 0 0 1px #1B3022 !important;
}

div:not([data-testid="stForm"]) input[data-testid="stTextInputField"] {
    background: transparent !important;
    border: none !important;
    padding: 0.5rem 0.3rem !important;
    color: #1B3022 !important;
    font-family: "Manrope", sans-serif !important;
    font-size: 1rem !important;
}

.sc-suggestions {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    align-items: center;
    gap: 0.5rem;
    max-width: 860px;
    margin: 0.75rem auto 1.5rem auto;
}

.sc-suggestion-label {
    font-size: 0.78rem;
    font-weight: 700;
    color: #7C8578;
    margin-right: 0.3rem;
}

.sc-badge-paused {
    display: inline-block;
    background: #C53030;
    color: #FFFFFF;
    font-size: 0.65rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    padding: 0.18rem 0.55rem;
    border-radius: 9999px;
    vertical-align: middle;
    margin-left: 0.6rem;
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


def _api(method: str, path: str, payload: dict | None = None):
    return api_client.api_request(method, path, payload)


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
        "pending_basket": None,
        "shop_response": None,
        "basket_response": None,
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
        "checkout_error": "",
        "checkout_order_id": None,
        "checkout_paypal_order_id": None,
        "checkout_order": None,
        "checkout_approve_url": "",
        "checkout_in_progress": False,
        "capture_status": "",
        "capture_in_progress": False,
        "approval_id": "",
        "approval_response": None,
        "approval_fingerprint": "",
        "step_up_confirmed": False,
        "step_up_error": "",
        "last_order": None,
        "success_message": "",
        "cart_budget_input": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)

    if not st.session_state.get("_demo_ready"):
        st.session_state._demo_ready = True
        st.session_state.readback = None
        st.session_state.pending_query = ""
        st.session_state.pending_budget_cents = None


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
            [2.3, 5.7, 1.4], vertical_alignment="center"
        )
        with logo_col:
            badge = '<span class="sc-badge-paused">PAUSED</span>' if paused else ""
            st.markdown(f'<div class="sc-logo">SafeCart{badge}</div>', unsafe_allow_html=True)
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


def _basket_budget(parsed):
    if parsed.total_budget_cents is not None:
        return parsed.total_budget_cents
    budgets = [item.budget_cents for item in parsed.items]
    if budgets and all(budget is not None for budget in budgets):
        return sum(budgets)
    return None


def _stage_parsed_request(parsed) -> bool:
    """Put a parsed request into session state and return True when it can be searched."""
    st.session_state.shop_response = None
    st.session_state.basket_response = None
    st.session_state.search_error = ""
    st.session_state.page = "Shop"

    if not parsed.items:
        st.session_state.readback = {"item": None, "budget_cents": None}
        st.session_state.pending_query = ""
        st.session_state.pending_budget_cents = None
        st.session_state.pending_basket = None
        st.session_state.notice = parser.readback_text(parsed)
        return False

    if len(parsed.items) > 1:
        budget = _basket_budget(parsed)
        readback = parser.readback_text(parsed)
        st.session_state.readback = {
            "item": None,
            "budget_cents": budget,
            "text": readback,
        }
        st.session_state.pending_query = ""
        st.session_state.pending_budget_cents = None

        if budget is None:
            st.session_state.pending_basket = None
            st.session_state.notice = (
                "Add one total budget, or a budget for every item, then search again."
            )
            return False
        if budget > config.MAX_BUDGET_CENTS:
            st.session_state.pending_basket = None
            st.session_state.notice = (
                f"The total budget is above the allowed maximum of "
                f"{format_usd(config.MAX_BUDGET_CENTS)}."
            )
            return False

        st.session_state.pending_basket = {
            "items": [item.query for item in parsed.items],
            "budget_cents": budget,
        }
        st.session_state.notice = "Review the read-back and confirm to search."
        return True

    item = parsed.items[0].query
    budget = parsed.items[0].budget_cents
    if budget is None:
        budget = parsed.total_budget_cents

    st.session_state.pending_basket = None
    st.session_state.readback = {"item": item, "budget_cents": budget}
    st.session_state.pending_query = item
    st.session_state.pending_budget_cents = budget

    if budget is None:
        st.session_state.notice = "Add a maximum budget (e.g. 'under $50'), then search again."
        return False
    st.session_state.notice = ""
    return True


def _handle_search(text: str) -> None:
    text = text.strip()
    if not text:
        st.session_state.notice = "Tell me what you want and your maximum budget (e.g. 'earbuds under $50')."
        return

    try:
        parsed = understand.parse_request(text)
    except parser.ParseError as error:
        st.session_state.notice = str(error)
        return

    can_search = _stage_parsed_request(parsed)
    if can_search:
        if isinstance(st.session_state.get("pending_basket"), dict):
            _run_basket_search()
        else:
            _run_search()


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
        st.session_state.basket_response = None
        st.session_state.search_error = ""
        st.session_state.readback = None
    else:
        st.session_state.shop_response = None
        st.session_state.search_error = error or "Product search failed."


def _run_basket_search() -> None:
    pending = st.session_state.get("pending_basket")
    if not isinstance(pending, dict):
        return
    items = pending.get("items")
    budget = pending.get("budget_cents")
    if not isinstance(items, list) or not items or budget is None:
        return

    data, error = api_client.search_basket(items, budget, limit=10)
    if isinstance(data, dict):
        st.session_state.basket_response = data
        st.session_state.shop_response = None
        st.session_state.search_error = ""
        st.session_state.readback = None
    else:
        st.session_state.basket_response = None
        st.session_state.search_error = error or "Basket search failed."


def _edit_request() -> None:
    st.session_state.readback = None
    st.session_state.pending_query = ""
    st.session_state.pending_budget_cents = None
    st.session_state.pending_basket = None
    st.session_state.shop_response = None
    st.session_state.basket_response = None
    st.session_state.search_error = ""
    st.session_state.page = "Shop"
    st.session_state.notice = "Update your request, then press Search."


def _render_search() -> None:
    with st.form("search_form", clear_on_submit=False):
        text_col, submit_col = st.columns([8.6, 1.8], vertical_alignment="center")
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


def _render_suggestions() -> None:
    if (
        st.session_state.get("shop_response")
        or st.session_state.get("basket_response")
        or st.session_state.get("readback")
    ):
        return
    st.markdown(
        '<div style="text-align: center; margin-top: 1.1rem; margin-bottom: 0.35rem;">'
        '<span style="font-size: 0.76rem; font-weight: 700; color: #7C8578; letter-spacing: 0.08em; text-transform: uppercase;">'
        'Popular Searches:</span></div>',
        unsafe_allow_html=True,
    )
    cols = st.columns(4)
    suggestions = [
        ("Wireless earbuds < $50", "find me wireless earbuds under fifty dollars"),
        ("Phone charger < $25", "find a phone charger under 25 dollars"),
        ("Mechanical keyboard < $80", "mechanical keyboard under eighty dollars"),
        ("Smart basket ($100)", "earbuds, phone charger, and a case, $100 total"),
    ]
    for idx, (label, query) in enumerate(suggestions):
        with cols[idx]:
            if st.button(label, key=f"sug_{idx}", type="secondary", use_container_width=True):
                st.session_state.search_input = query
                _handle_search(query)
                st.rerun()


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
    if st.session_state.get("shop_response") or st.session_state.get("basket_response"):
        return
    readback = st.session_state.get("readback")
    if not isinstance(readback, dict):
        return

    item = readback.get("item")
    budget = readback.get("budget_cents")
    text = readback.get("text")

    if text:
        body = html.escape(str(text))
    elif item and budget is not None:
        body = (
            f"Understood: <mark>{html.escape(str(item))}</mark>, "
            f"<mark>maximum {html.escape(_safe_usd(budget))}</mark>. "
            "Is that right? I will search for live listings."
        )
    elif item:
        body = (
            f"Looking for: <mark>{html.escape(str(item))}</mark>. "
            "Please specify a maximum budget (e.g. 'under $50') before I search."
        )
    else:
        body = "I could not tell what you want to buy. Please try another request (e.g. 'earbuds under $50')."

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
            pending_basket = st.session_state.get("pending_basket")
            can_basket = (
                isinstance(pending_basket, dict)
                and bool(pending_basket.get("items"))
                and pending_basket.get("budget_cents") is not None
            )
            can_single = bool(st.session_state.pending_query) and (
                st.session_state.pending_budget_cents is not None
            )
            if st.button(
                "Confirm & Search",
                type="primary",
                use_container_width=True,
                disabled=not (can_basket or can_single),
                key="confirm_search",
            ):
                if can_basket:
                    _run_basket_search()
                else:
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


def _clear_approval_state() -> None:
    st.session_state.approval_id = ""
    st.session_state.approval_response = None
    st.session_state.approval_fingerprint = ""
    st.session_state.step_up_confirmed = False
    st.session_state.step_up_error = ""


def _clear_order_state() -> None:
    st.session_state.checkout_order_id = None
    st.session_state.checkout_paypal_order_id = None
    st.session_state.checkout_order = None
    st.session_state.checkout_approve_url = ""
    st.session_state.checkout_status = ""
    st.session_state.capture_status = ""
    st.session_state.checkout_in_progress = False
    st.session_state.capture_in_progress = False


def _invalidate_checkout_state(clear_success: bool = False) -> None:
    _clear_approval_state()
    _clear_order_state()
    st.session_state.checkout_error = ""
    if clear_success:
        st.session_state.last_order = None
        st.session_state.success_message = ""


def _safe_http_url(url) -> bool:
    if not isinstance(url, str):
        return False
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _add_to_cart(product: dict, source=None) -> None:
    if st.session_state.get("checkout_order_id"):
        st.session_state.notice = "Finish or cancel the current order before changing the cart."
        return

    ranking = product.get("ranking") if isinstance(product.get("ranking"), dict) else {}
    offer = ranking.get("offer") if isinstance(ranking.get("offer"), dict) else {}
    response = st.session_state.get("shop_response")
    source = str(
        source
        if source is not None
        else (response.get("source") if isinstance(response, dict) else "")
        or ""
    )
    product_id = str(product.get("id") or "")
    domain = str(offer.get("domain") or "")
    title = str(product.get("title") or "Untitled listing")
    price_cents = offer.get("price_cents")
    url = str(offer.get("url") or "")
    image_url = str(product.get("image_url") or "")

    if not source:
        st.session_state.notice = "This item is missing its catalog source. Search again."
        return

    for item in st.session_state.cart:
        if item.get("product_id") == product_id:
            st.session_state.notice = "This item is already in your cart."
            return
        if item.get("source") and item.get("source") != source:
            st.session_state.notice = (
                "Items from different catalog sources cannot be bought together."
            )
            return

    _invalidate_checkout_state(clear_success=True)
    st.session_state.cart.append(
        {
            "product_id": product_id,
            "title": title,
            "domain": domain,
            "price_cents": price_cents,
            "url": url,
            "image_url": image_url,
            "source": source,
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
        cart_pids = {item.get("product_id") for item in st.session_state.get("cart") or []}
        pid = product.get("id")
        is_in_cart = pid in cart_pids
        btn_label = "In Cart ✓" if is_in_cart else "Add to Cart 🛒"
        btn_type = "primary" if is_in_cart else "secondary"
        if st.button(btn_label, key=f"add_cart_{pid}_{domain}", type=btn_type):
            if is_in_cart:
                st.session_state.notice = "This item is already in your cart. Review it in the Cart tab."
            else:
                _add_to_cart(product)
            st.rerun()


def _render_explanation(explanation, source) -> None:
    if not explanation:
        return
    label = "AI" if str(source) == "gemini" else "Rules"
    with st.container(border=True, key="why_this_pick"):
        st.markdown(
            '<div class="sc-readback">'
            f'<span class="sc-ai-label">{label}</span>'
            f'<span class="sc-readback-text">{html.escape(str(explanation))}</span>'
            "</div>",
            unsafe_allow_html=True,
        )


def _summary_products(summary) -> list:
    chosen = summary.get("chosen") if isinstance(summary, dict) else None
    if not isinstance(chosen, list):
        return []
    products = []
    for entry in chosen:
        product = entry.get("product") if isinstance(entry, dict) else None
        if isinstance(product, dict):
            products.append(product)
    return products


def _render_basket_results(data: dict) -> None:
    source = str(data.get("source") or "")
    optimal = data.get("optimal") if isinstance(data.get("optimal"), dict) else {}
    greedy = data.get("greedy") if isinstance(data.get("greedy"), dict) else {}
    chosen = _summary_products(optimal)
    total_cents = optimal.get("total_price_cents")
    budget_cents = data.get("total_budget_cents")

    st.markdown(
        '<div class="sc-results-head">'
        "<h2>Smart basket</h2>"
        f'<span class="sc-results-meta">{html.escape(_safe_usd(total_cents))} '
        f'of {html.escape(_safe_usd(budget_cents))}</span>'
        "</div>",
        unsafe_allow_html=True,
    )

    _render_explanation(data.get("explanation"), data.get("explanation_source"))

    if not chosen:
        st.markdown(
            '<div class="sc-empty">No safe basket fits that total budget. '
            "Try fewer items or a higher budget.</div>",
            unsafe_allow_html=True,
        )
    else:
        if source:
            if st.button(
                "Add all basket items to cart",
                type="primary",
                use_container_width=True,
                key="add_whole_basket",
            ):
                for product in chosen:
                    _add_to_cart(product, source=source)
                st.session_state.notice = (
                    f"Added {len(chosen)} item(s) from the optimal basket."
                )
                st.rerun()
        else:
            st.warning(
                "These items came from different catalog sources and cannot be bought together."
            )

        for index, product in enumerate(chosen):
            with st.container(border=True):
                _product_card(product, show_cart_button=False)
                if source and st.button(
                    "Add to Cart 🛒",
                    key=f"basket_add_{index}_{product.get('id')}",
                    type="secondary",
                ):
                    _add_to_cart(product, source=source)
                    st.rerun()

    with st.container(border=True):
        st.markdown("**Greedy vs optimal**")
        left, right = st.columns(2)
        with left:
            st.markdown(
                f"**Optimal** &middot; {html.escape(_safe_usd(optimal.get('total_price_cents')))} "
                f"&middot; score {html.escape(str(optimal.get('total_score', 0)))}"
            )
            for product in _summary_products(optimal):
                offer = (product.get("ranking") or {}).get("offer") or {}
                st.write(
                    f"{product.get('title', 'Untitled listing')} - "
                    f"{_safe_usd(offer.get('price_cents'))}"
                )
        with right:
            st.markdown(
                f"**Greedy** &middot; {html.escape(_safe_usd(greedy.get('total_price_cents')))} "
                f"&middot; score {html.escape(str(greedy.get('total_score', 0)))}"
            )
            for product in _summary_products(greedy):
                offer = (product.get("ranking") or {}).get("offer") or {}
                st.write(
                    f"{product.get('title', 'Untitled listing')} - "
                    f"{_safe_usd(offer.get('price_cents'))}"
                )
        score_gain = data.get("score_gain")
        difference = data.get("price_difference_cents")
        if isinstance(score_gain, (int, float)) and not isinstance(score_gain, bool):
            st.caption(f"The optimizer gained {score_gain:+.2f} match score.")
        if isinstance(difference, int) and not isinstance(difference, bool):
            if difference > 0:
                st.caption(f"The optimal basket spends {_safe_usd(difference)} more.")
            elif difference < 0:
                st.caption(f"The optimal basket spends {_safe_usd(-difference)} less.")

    notes = []
    items = data.get("items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("note"):
                notes.append(f"{item.get('query')}: {item.get('note')}")
    if notes:
        with st.expander("Basket notes", expanded=False):
            for note in notes:
                st.write(note)
    if data.get("note"):
        st.caption(str(data.get("note")))


def _render_results() -> None:
    if st.session_state.page != "Shop":
        return
    basket_response = st.session_state.get("basket_response")
    if isinstance(basket_response, dict):
        _render_basket_results(basket_response)
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

    _render_explanation(response.get("explanation"), response.get("explanation_source"))

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
        result = voice.transcribe_clip(clip, transcribe=llm.transcribe_audio)
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
                    if _stage_parsed_request(parsed):
                        if isinstance(st.session_state.get("pending_basket"), dict):
                            _run_basket_search()
                        else:
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


def _render_checkout_error() -> None:
    message = st.session_state.get("checkout_error")
    if message:
        st.markdown(
            f'<div class="sc-error">{html.escape(str(message))}</div>',
            unsafe_allow_html=True,
        )


def _render_order_details(order: dict, currency: str, heading: str) -> None:
    order_id = order.get("id")
    paypal_order_id = order.get("paypal_order_id")
    status = str(order.get("status") or "UNKNOWN")
    total_cents = order.get("total_cents")
    created_at = flow.format_epoch(order.get("created_at"))
    captured_at = flow.format_epoch(order.get("captured_at"))
    capture_id = order.get("capture_id")

    with st.container(border=True):
        st.markdown(f"**{html.escape(heading)}**")
        st.write(f"Order ID: {order_id if order_id is not None else 'unknown'}")
        st.write(f"PayPal order ID: {paypal_order_id or 'not returned'}")
        st.write(f"Payment status: {status}")
        st.write(f"Total amount: {_safe_usd(total_cents)} {html.escape(str(currency or 'USD'))}")
        st.write(f"Created: {created_at or 'not available'}")
        st.write(f"Captured: {captured_at or 'not available'}")
        if capture_id:
            st.write(f"Capture ID: {capture_id}")

        if status == "CREATED":
            st.caption(
                "The PayPal order exists, but payment is not complete. Approve it on PayPal, "
                "then capture it below."
            )
        elif status == "CAPTURED":
            st.caption("The backend confirms this payment as CAPTURED.")
        elif status == "FAILED":
            st.caption("The backend reports this order as FAILED. No successful payment was confirmed.")


def _render_last_success(currency: str) -> bool:
    message = st.session_state.get("success_message")
    order = st.session_state.get("last_order")
    if not message:
        return False
    st.markdown(
        f'<div class="sc-notice">{html.escape(str(message))}</div>',
        unsafe_allow_html=True,
    )
    if isinstance(order, dict):
        _render_order_details(order, currency, "Confirmed order")
    return True


def _render_budget_input():
    if not st.session_state.get("cart_budget_input"):
        pending = st.session_state.get("pending_budget_cents")
        if isinstance(pending, int) and not isinstance(pending, bool) and pending > 0:
            st.session_state.cart_budget_input = format_usd(pending)
        else:
            cart = st.session_state.get("cart") or []
            total_est = sum(item.get("price_cents") or 0 for item in cart)
            if total_est > 0:
                st.session_state.cart_budget_input = format_usd(total_est)
            else:
                st.session_state.cart_budget_input = ""

    st.markdown("### Purchase budget")
    budget_text = st.text_input(
        "Maximum total for this purchase (USD)",
        key="cart_budget_input",
        help="The backend enforces this limit. Prices shown from search are estimates.",
    )

    budget_cents = None
    budget_error = ""
    if str(budget_text or "").strip():
        try:
            budget_cents = parse_dollars_to_cents(budget_text)
        except MoneyError:
            budget_error = "Enter a valid dollar amount, for example 50 or 49.99."
            st.markdown(
                f'<div class="sc-error">{html.escape(budget_error)}</div>',
                unsafe_allow_html=True,
            )
    else:
        st.caption("Enter a purchase budget before creating an approval.")

    return budget_cents, budget_error


def _render_cart_items(review: dict, locked: bool) -> None:
    for idx, item in enumerate(review.get("items") or []):
        price_cents = item.get("verified_price_cents")
        price_label = "Verified approval price"
        if price_cents is None:
            price_cents = item.get("price_cents")
            price_label = "Estimated search price"

        title = html.escape(str(item.get("title") or "Untitled listing"))
        domain = html.escape(str(item.get("domain") or "Unknown merchant"))
        price = html.escape(_safe_usd(price_cents))
        quantity = item.get("quantity") or 1
        url = str(item.get("url") or "")
        image_url = str(item.get("image_url") or "")

        with st.container(border=True):
            image_col, detail_col = st.columns([1.2, 4.8], vertical_alignment="center")
            with image_col:
                if _safe_http_url(image_url):
                    st.image(image_url, use_container_width=True)
                else:
                    st.markdown(
                        '<div style="width: 68px; height: 68px; background: #FCF8EE; border: 1px solid #E8DEC5; '
                        'border-radius: 8px; display: flex; align-items: center; justify-content: center; '
                        'font-size: 1.8rem; margin: 0 auto;">🛍️</div>',
                        unsafe_allow_html=True,
                    )
            with detail_col:
                link_html = ""
                if _safe_http_url(url):
                    safe_url = html.escape(url, quote=True)
                    link_html = (
                        f' <a class="sc-listing-link" href="{safe_url}" '
                        'target="_blank" rel="noopener noreferrer">View listing</a>'
                    )
                st.markdown(
                    '<div class="sc-product">'
                    '<div class="sc-product-top">'
                    f'<span class="sc-product-domain">{domain}</span>'
                    f'<span class="sc-product-price">{price}</span>'
                    "</div>"
                    f"<h3>{title}</h3>"
                    f'<div class="sc-product-meta">Quantity: {int(quantity)} &middot; {price_label}</div>'
                    f'<div class="sc-product-foot">{link_html}</div>'
                    "</div>",
                    unsafe_allow_html=True,
                )
                if not locked and st.button(
                    "Remove", key=f"remove_cart_{idx}", type="secondary"
                ):
                    st.session_state.cart.pop(idx)
                    _invalidate_checkout_state()
                    st.rerun()


def _render_purchase_summary(review: dict, currency: str) -> None:
    estimated = review.get("estimated_total_cents")
    verified = review.get("verified_total_cents")
    budget = review.get("budget_cents")
    remaining = review.get("remaining_budget_cents")
    remaining_cap = review.get("remaining_daily_cap_cents")

    col_est, col_verified, col_budget, col_cap = st.columns(4)
    col_est.metric("Estimated total", _safe_usd(estimated))
    col_verified.metric(
        "Verified approval total",
        _safe_usd(verified) if verified is not None else "Not approved",
    )
    col_budget.metric("Purchase budget", _safe_usd(budget) if budget is not None else "Not set")
    col_cap.metric(
        "Remaining daily cap",
        _safe_usd(remaining_cap) if remaining_cap is not None else "Unknown",
    )

    remaining_text = (
        _safe_usd(remaining) if remaining is not None else "Not available"
    )
    st.caption(
        f"Currency: {currency}. Remaining purchase budget: {remaining_text}. "
        "Search prices are estimates only. The backend-verified approval amount is authoritative, "
        "and the backend rechecks every live price again at checkout."
    )


def _render_approval_section(cart, budget_cents, budget_error, source) -> None:
    approval_response = st.session_state.get("approval_response")
    approval_payload = (
        approval_response.get("payload")
        if isinstance(approval_response, dict)
        else None
    )

    if approval_response and not flow.approval_matches_cart(
        approval_payload, cart, budget_cents, source
    ):
        _clear_approval_state()
        approval_response = None
        approval_payload = None
        st.warning("The cart or budget changed since approval. Approve the purchase again.")

    if approval_response:
        st.success("The backend created a signed approval for this exact purchase.")
        verified_total = (
            approval_payload.get("total_cents")
            if isinstance(approval_payload, dict)
            else None
        )
        st.markdown(
            f"Backend-verified checkout amount: **{html.escape(_safe_usd(verified_total))}**"
        )
        if st.button("Clear approval and review again", key="clear_approval", type="secondary"):
            _clear_approval_state()
            st.session_state.checkout_error = ""
            st.rerun()
        return

    can_approve = (
        bool(cart)
        and budget_cents is not None
        and not budget_error
        and bool(source)
    )
    if st.button(
        "Approve Purchase",
        type="primary",
        key="approve_purchase",
        disabled=not can_approve,
        use_container_width=True,
    ):
        candidates, candidate_source, candidate_error = flow.build_candidates(cart)
        if candidate_error:
            st.session_state.checkout_error = candidate_error
        else:
            with st.spinner("Requesting a signed backend approval..."):
                data, error = api_client.create_approval(
                    candidates, budget_cents, candidate_source
                )
            if error:
                st.session_state.checkout_error = error
            elif not isinstance(data, dict):
                st.session_state.checkout_error = "The backend returned an unexpected approval response."
            elif not data.get("ok"):
                st.session_state.checkout_error = flow.friendly_error(
                    data.get("code"), data.get("reason")
                )
            elif not data.get("approval_id"):
                st.session_state.checkout_error = "The backend did not return an approval id."
            else:
                payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
                st.session_state.approval_id = data["approval_id"]
                st.session_state.approval_response = data
                st.session_state.approval_fingerprint = flow.approval_fingerprint(
                    cart, budget_cents, candidate_source
                )
                st.session_state.step_up_confirmed = not bool(
                    payload.get("step_up_required", True)
                )
                st.session_state.step_up_error = ""
                st.session_state.checkout_error = ""
                st.session_state.notice = "Purchase approved by the backend."
        st.rerun()


def _render_step_up_section(approval_response) -> None:
    payload = approval_response.get("payload") if isinstance(approval_response, dict) else None
    if not isinstance(payload, dict):
        return

    if not payload.get("step_up_required"):
        st.caption("No additional typed confirmation is required for this purchase.")
        return

    if st.session_state.get("step_up_confirmed"):
        st.success("The backend accepted the step-up confirmation.")
        return

    total_cents = payload.get("total_cents")
    reasons = payload.get("step_up_reasons") or []
    st.warning("Additional confirmation is required before this purchase can proceed.")
    st.markdown(f"Exact amount requiring confirmation: **{html.escape(_safe_usd(total_cents))}**")
    if reasons:
        st.caption("Reasons: " + ", ".join(str(reason).replace("_", " ") for reason in reasons))

    typed = st.text_input(
        "Type CONFIRM or the exact approved total",
        key="step_up_input",
        max_chars=40,
    )
    if st.button(
        "Confirm step-up",
        type="primary",
        key="submit_step_up",
        disabled=not str(typed or "").strip(),
    ):
        with st.spinner("Confirming with the backend..."):
            data, error = api_client.confirm_step_up(
                st.session_state.get("approval_id"), str(typed or "").strip()
            )
        if error:
            st.session_state.step_up_error = error
        elif not isinstance(data, dict):
            st.session_state.step_up_error = "The backend returned an unexpected step-up response."
        elif not data.get("ok"):
            st.session_state.step_up_error = flow.friendly_error(
                data.get("code"), data.get("reason")
            )
        else:
            st.session_state.step_up_confirmed = True
            st.session_state.step_up_error = ""
        st.rerun()

    if st.session_state.get("step_up_error"):
        st.markdown(
            f'<div class="sc-error">{html.escape(str(st.session_state.step_up_error))}</div>',
            unsafe_allow_html=True,
        )


def _fetch_order(order_id):
    data, error = api_client.get_order(order_id)
    if isinstance(data, dict):
        st.session_state.checkout_order = data
        st.session_state.checkout_paypal_order_id = data.get("paypal_order_id")
        st.session_state.checkout_status = str(data.get("status") or "")
    return data, error


def _mark_captured(order: dict) -> None:
    order_id = order.get("id")
    message = (
        f"Payment captured successfully. The backend confirms order {order_id} as CAPTURED."
    )
    _clear_approval_state()
    st.session_state.checkout_order = order
    st.session_state.checkout_order_id = None
    st.session_state.checkout_paypal_order_id = order.get("paypal_order_id")
    st.session_state.checkout_approve_url = ""
    st.session_state.checkout_status = "CAPTURED"
    st.session_state.capture_status = "CAPTURED"
    st.session_state.capture_in_progress = False
    st.session_state.checkout_in_progress = False
    st.session_state.checkout_error = ""
    st.session_state.last_order = order
    st.session_state.success_message = message
    st.session_state.cart = []
    st.session_state.cart_budget_input = None


def _process_checkout_result(data, error) -> None:
    if error:
        st.session_state.checkout_status = "UNKNOWN"
        st.session_state.checkout_error = error
        return
    if not isinstance(data, dict):
        st.session_state.checkout_status = "UNKNOWN"
        st.session_state.checkout_error = (
            "The backend returned an unexpected checkout response. The cart was kept."
        )
        return

    if data.get("ok"):
        order_id = data.get("order_id")
        st.session_state.checkout_order_id = order_id
        st.session_state.checkout_approve_url = str(data.get("approve_url") or "")
        st.session_state.checkout_status = "CREATED"
        st.session_state.checkout_error = ""
        st.session_state.capture_status = ""
        order, fetch_error = _fetch_order(order_id)
        if flow.is_captured_order(order):
            _mark_captured(order)
        elif not isinstance(order, dict):
            st.session_state.notice = (
                "The PayPal order was created, but its details could not be loaded. "
                "Use Refresh order status before capture."
            )
        _clear_approval_state()
        return

    code = str(data.get("code") or "")
    st.session_state.checkout_status = code or "REFUSED"
    st.session_state.checkout_error = flow.friendly_error(code, data.get("reason"))
    order_id = data.get("order_id")
    if order_id:
        st.session_state.checkout_order_id = order_id
        order, _ = _fetch_order(order_id)
        if flow.is_captured_order(order):
            _mark_captured(order)
            return
    if code in RESET_APPROVAL_CODES:
        _clear_approval_state()


def _render_checkout_section(approval_response, currency: str) -> None:
    payload = approval_response.get("payload") if isinstance(approval_response, dict) else None
    if not isinstance(payload, dict):
        return

    step_up_ok = (
        not payload.get("step_up_required", True)
        or bool(st.session_state.get("step_up_confirmed"))
    )
    if not step_up_ok:
        st.caption("Complete the typed step-up confirmation before checkout.")
        return

    total_cents = payload.get("total_cents")
    st.markdown(
        f"Backend-verified checkout amount: **{html.escape(_safe_usd(total_cents))} "
        f"{html.escape(str(currency or 'USD'))}**"
    )
    can_checkout = bool(st.session_state.get("approval_id")) and not st.session_state.get(
        "checkout_in_progress"
    )
    if st.button(
        "Pay with PayPal Sandbox",
        type="primary",
        key="checkout_cart",
        disabled=not can_checkout,
        use_container_width=True,
    ):
        st.session_state.checkout_in_progress = True
        try:
            with st.spinner("Creating the PayPal sandbox order..."):
                data, error = api_client.execute_checkout(
                    st.session_state.get("approval_id")
                )
            _process_checkout_result(data, error)
        finally:
            st.session_state.checkout_in_progress = False
        st.rerun()


def _render_order_section(currency: str) -> None:
    order_id = st.session_state.get("checkout_order_id")
    order = st.session_state.get("checkout_order")

    if not order_id:
        if isinstance(order, dict):
            _render_order_details(order, currency, "Order result")
        return

    st.markdown("### Checkout result")
    if isinstance(order, dict):
        _render_order_details(order, currency, "PayPal sandbox order")
    else:
        st.markdown(f"Order ID: **{html.escape(str(order_id))}**")
        st.caption("Order details have not been loaded yet.")

    approve_url = str(st.session_state.get("checkout_approve_url") or "")
    if approve_url:
        if flow.is_safe_paypal_sandbox_url(approve_url):
            st.link_button("Approve on PayPal (Sandbox)", approve_url)
        else:
            st.warning(
                "A PayPal approval link was returned, but it failed URL safety validation "
                "and was not displayed."
            )

    if st.button("Refresh order status", key="refresh_order", type="secondary"):
        refreshed, refresh_error = _fetch_order(order_id)
        if not isinstance(refreshed, dict):
            st.session_state.checkout_error = (
                refresh_error
                or "The order status could not be refreshed. The cart was kept."
            )
        elif flow.is_captured_order(refreshed):
            _mark_captured(refreshed)
        else:
            st.session_state.checkout_error = ""
            st.session_state.notice = (
                f"The backend reports order {order_id} as {refreshed.get('status') or 'UNKNOWN'}."
            )
        st.rerun()

    order_status = str(order.get("status") or "") if isinstance(order, dict) else ""
    if order_status == "CREATED" and not st.session_state.get("capture_in_progress"):
        if st.button(
            "I approved — capture payment",
            type="primary",
            key="capture_payment",
            use_container_width=True,
        ):
            st.session_state.capture_in_progress = True
            try:
                preflight, preflight_error = api_client.get_order(order_id)
                if not isinstance(preflight, dict):
                    st.session_state.capture_status = "UNKNOWN"
                    st.session_state.checkout_error = (
                        preflight_error
                        or "The order status could not be verified before capture. "
                        "The cart was kept. Use Refresh order status."
                    )
                else:
                    st.session_state.checkout_order = preflight
                    preflight_status = str(preflight.get("status") or "")
                    if flow.is_captured_order(preflight):
                        _mark_captured(preflight)
                    elif preflight_status != "CREATED":
                        st.session_state.capture_status = preflight_status or "UNKNOWN"
                        st.session_state.checkout_error = flow.friendly_error(
                            preflight_status,
                            f"This order is {preflight_status or 'unknown'} and cannot be captured. "
                            "The cart was kept.",
                        )
                    else:
                        result, result_error = api_client.complete_order(order_id)
                        final_order, final_error = api_client.get_order(order_id)
                        if isinstance(final_order, dict):
                            st.session_state.checkout_order = final_order
                            final_status = str(final_order.get("status") or "")
                            if flow.is_captured_order(final_order):
                                _mark_captured(final_order)
                            elif final_status == "CREATED":
                                st.session_state.capture_status = "CREATED"
                                if result_error:
                                    st.session_state.checkout_error = (
                                        "The capture result is uncertain, and the backend still "
                                        "reports the order as CREATED. The cart was kept. Do not "
                                        "retry automatically; use Refresh order status."
                                    )
                                else:
                                    code = (
                                        result.get("code")
                                        if isinstance(result, dict)
                                        else "WAITING_APPROVAL"
                                    )
                                    st.session_state.checkout_error = flow.friendly_error(
                                        code or "WAITING_APPROVAL"
                                    )
                            else:
                                st.session_state.capture_status = final_status or "UNKNOWN"
                                st.session_state.checkout_error = flow.friendly_error(
                                    final_status,
                                    "The order did not reach CAPTURED. The cart was kept.",
                                )
                        else:
                            st.session_state.capture_status = "UNKNOWN"
                            st.session_state.checkout_error = (
                                final_error
                                or "The capture result could not be confirmed because the order "
                                "status could not be fetched. The cart was kept. Do not retry "
                                "automatically; use Refresh order status."
                            )
                _clear_approval_state()
            finally:
                st.session_state.capture_in_progress = False
            st.rerun()

    if order_status in ("FAILED", "CANCELLED"):
        if st.button(
            "Start a new approval for this cart",
            key="restart_after_failed_order",
            type="secondary",
        ):
            _invalidate_checkout_state()
            st.rerun()


def _render_cart_page() -> None:
    if st.session_state.page != "Cart":
        return

    currency = str(st.session_state.status.get("currency") or config.CURRENCY or "USD")
    had_success = _render_last_success(currency)

    cart = st.session_state.cart
    order_exists = bool(st.session_state.get("checkout_order_id"))

    col_head, col_clear = st.columns([8, 2], vertical_alignment="center")
    with col_head:
        st.markdown('<h1 class="sc-page-title" style="margin: 0.8rem 0;">Cart</h1>', unsafe_allow_html=True)
    with col_clear:
        if bool(cart) and not order_exists and st.button("Clear Cart", key="clear_all_cart", type="secondary", use_container_width=True):
            st.session_state.cart = []
            _invalidate_checkout_state()
            st.rerun()

    if not cart:
        if not had_success:
            st.markdown(
                '<div class="sc-empty">Your cart is empty. Add items from the Shop tab.</div>',
                unsafe_allow_html=True,
            )
        return

    order_exists = bool(st.session_state.get("checkout_order_id"))
    source = flow.cart_source(cart)

    budget_cents = None
    budget_error = ""
    if not order_exists:
        budget_cents, budget_error = _render_budget_input()

    approval_response = st.session_state.get("approval_response")
    approval_payload = (
        approval_response.get("payload")
        if isinstance(approval_response, dict)
        else None
    )
    review = flow.purchase_review(
        cart,
        budget_cents,
        st.session_state.status.get("remaining_daily_cap_cents"),
        currency,
        approval_payload,
    )

    _render_cart_items(review, locked=order_exists)
    _render_purchase_summary(review, currency)

    if order_exists:
        _render_checkout_error()
        _render_order_section(currency)
        return

    _render_approval_section(cart, budget_cents, budget_error, source)
    approval_response = st.session_state.get("approval_response")
    if isinstance(approval_response, dict):
        _render_step_up_section(approval_response)
        _render_checkout_section(approval_response, currency)
    _render_checkout_error()


def _render_activity_page() -> None:
    if st.session_state.page != "Activity":
        return
    st.markdown('<h1 class="sc-page-title">Activity & Orders</h1>', unsafe_allow_html=True)

    col_hdr, col_btn = st.columns([8, 2], vertical_alignment="center")
    with col_hdr:
        st.caption("All orders created and paid through PayPal Sandbox.")
    with col_btn:
        if st.button("Refresh", key="refresh_activity", type="secondary", use_container_width=True):
            st.rerun()

    data, error = _api("GET", "/api/orders")
    if error:
        st.markdown(f'<div class="sc-error">{html.escape(error)}</div>', unsafe_allow_html=True)
        return

    orders = data.get("orders") if isinstance(data, dict) else []
    if not orders:
        st.markdown(
            '<div class="sc-empty">No orders found yet. Search for products in the '
            '<strong>Shop</strong> tab and click "Add to Cart" to start checkout.</div>',
            unsafe_allow_html=True,
        )
        return

    currency = str(st.session_state.status.get("currency") or config.CURRENCY or "USD")
    for order in orders:
        _render_order_details(order, str(order.get("currency") or currency), f"Order #{order.get('id')}")


def _render_audit_log_page() -> None:
    if st.session_state.page != "Log":
        return
    st.markdown('<h1 class="sc-page-title">Safety & Security Audit Log</h1>', unsafe_allow_html=True)

    col_hdr, col_btn = st.columns([8, 2], vertical_alignment="center")
    with col_hdr:
        st.caption("Cryptographic audit trail tracking all safety rule decisions, injection checks, and payments.")
    with col_btn:
        if st.button("Refresh", key="refresh_logs", type="secondary", use_container_width=True):
            st.rerun()

    data, error = _api("GET", "/api/logs")
    if error:
        st.markdown(f'<div class="sc-error">{html.escape(error)}</div>', unsafe_allow_html=True)
        return

    logs = data.get("logs") if isinstance(data, dict) else []
    if not logs:
        st.markdown(
            '<div class="sc-empty">No audit logs recorded yet.</div>',
            unsafe_allow_html=True,
        )
        return

    for entry in logs:
        actor = html.escape(str(entry.get("actor") or "system"))
        action = html.escape(str(entry.get("action") or ""))
        detail = html.escape(str(entry.get("detail") or ""))
        ts = html.escape(str(entry.get("ts") or ""))
        ref = entry.get("ref")
        ref_text = f" &middot; Ref: <code>{html.escape(str(ref))}</code>" if ref else ""

        badge_color = "#1B3022"
        badge_bg = "#FFF3D5"
        if "injection" in action or "blocked" in action:
            badge_color = "#991B1B"
            badge_bg = "#FEE2E2"
        elif actor == "paypal":
            badge_color = "#1E40AF"
            badge_bg = "#DBEAFE"
        elif actor == "guardrail":
            badge_color = "#92400E"
            badge_bg = "#FEF3C7"

        st.markdown(
            '<div style="margin-bottom: 0.75rem; padding: 0.85rem 1.15rem; '
            'background: #FFFFFF; border: 1px solid #E8DEC5; border-radius: 8px;">'
            '<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.35rem;">'
            f'<span><span style="background: {badge_bg}; color: {badge_color}; border-radius: 9999px; '
            f'padding: 0.15rem 0.55rem; font-size: 0.72rem; font-weight: 700; text-transform: uppercase;">'
            f'{actor}</span> '
            f'<strong style="margin-left: 0.45rem; font-size: 0.88rem; color: #1B3022;">{action}</strong></span>'
            f'<span style="font-size: 0.75rem; color: #7C8578;">{ts}</span>'
            '</div>'
            f'<div style="font-size: 0.86rem; color: #2D3B2D; line-height: 1.45;">{detail}{ref_text}</div>'
            '</div>',
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
    _render_suggestions()
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
elif st.session_state.page == "Activity":
    _render_notice()
    _render_activity_page()
elif st.session_state.page == "Log":
    _render_notice()
    _render_audit_log_page()
else:
    _render_notice()
    st.session_state.page = "Shop"
    st.rerun()
