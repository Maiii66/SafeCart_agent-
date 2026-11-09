import html

import streamlit as st


def render_activity_page(api_request, config, render_order_details) -> None:
    """Render the order activity page."""
    if st.session_state.page != "Activity":
        return
    st.markdown('<h1 class="sc-page-title">Activity & Orders</h1>', unsafe_allow_html=True)

    col_hdr, col_btn = st.columns([8, 2], vertical_alignment="center")
    with col_hdr:
        st.caption("All orders created and paid through PayPal Sandbox.")
    with col_btn:
        if st.button("Refresh", key="refresh_activity", type="secondary", use_container_width=True):
            st.rerun()

    data, error = api_request("GET", "/api/orders")
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
        render_order_details(
            order,
            str(order.get("currency") or currency),
            f"Order #{order.get('id')}",
        )


def render_audit_log_page(api_request) -> None:
    """Render the safety and security audit log page."""
    if st.session_state.page != "Log":
        return
    st.markdown('<h1 class="sc-page-title">Safety & Security Audit Log</h1>', unsafe_allow_html=True)

    col_hdr, col_btn = st.columns([8, 2], vertical_alignment="center")
    with col_hdr:
        st.caption("Cryptographic audit trail tracking all safety rule decisions, injection checks, and payments.")
    with col_btn:
        if st.button("Refresh", key="refresh_logs", type="secondary", use_container_width=True):
            st.rerun()

    data, error = api_request("GET", "/api/logs")
    if error:
        st.markdown(f'<div class="sc-error">{html.escape(error)}</div>', unsafe_allow_html=True)
        return

    logs = data.get("logs") if isinstance(data, dict) else []
    if not logs:
        st.markdown('<div class="sc-empty">No audit logs recorded yet.</div>', unsafe_allow_html=True)
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
