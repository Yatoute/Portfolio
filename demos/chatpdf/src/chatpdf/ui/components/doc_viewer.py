from __future__ import annotations

import streamlit as st

from chatpdf.ui.api.client import ChatPDFClient
from chatpdf.ui.api.errors import ApiError
from chatpdf.ui.caching import cached_get_pdf_bytes
from chatpdf.ui.components.notifications import error_box, info_banner
from chatpdf.ui.components.page_viewer import render_page_viewer, get_pdf_page_count


def render_pdf_panel(client: ChatPDFClient):
    ui = st.session_state.ui

    if not ui.index_id:
        info_banner("No document", "Upload a PDF to preview it here.")
        return

    try:
        pdf_bytes = cached_get_pdf_bytes(ui.api_base_url, ui.index_id)
    except ApiError as e:
        error_box("Failed to load PDF", e)
        return

    try:
        total_pages = get_pdf_page_count(pdf_bytes)
    except Exception as e:
        error_box("Invalid PDF", e)
        return

    state_key = "pdf_page"

    if state_key not in st.session_state:
        st.session_state[state_key] = 1

    render_page_viewer(
        pdf_bytes=pdf_bytes,
        selected_page=st.session_state[state_key],
        total_pages=total_pages,
        state_key=state_key,
        label=str(st.session_state[state_key]),
        dpi=220,
        show_go_to_page=True,
        fragment_scoped=False,
    )