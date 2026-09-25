from __future__ import annotations

import streamlit as st

from chatpdf.ui.api.client import ChatPDFClient
from chatpdf.ui.caching import bust_index_cache, bust_pdf_cache, bust_messages_cache
from chatpdf.ui.components.notifications import error_box, success_toast, info_banner
from chatpdf.ui.components import (
    render_uploader,
    render_chat,
    render_pdf_panel,
    render_sidebar,
)
from chatpdf.ui.flows import run_upload


def render():
    ui = st.session_state.get("ui")
    
    if ui is None:
        return
    
    client = ChatPDFClient(ui.api_base_url)

    st.title("📄 ChatPDF")
    st.caption("Upload a PDF, preview it, and chat with it.")

    # -------------------------
    # Sidebar
    # -------------------------
    with st.sidebar:

        # Upload section (always on top)
        with st.expander("Upload", expanded=(ui.index_id is None)):
            payload = render_uploader(ui)

            if payload and payload["submit"]:
                try:
                    run_upload(
                        client,
                        file=payload["file"],
                        extract_images=payload["extract_images"],
                        start_page=payload["start_page"],
                        max_pages=payload["max_pages"],
                    )

                    # Clear caches when new document is indexed
                    bust_index_cache()
                    bust_pdf_cache()
                    bust_messages_cache()

                    success_toast("Document indexed successfully")
                    st.rerun()

                except Exception as e:
                    error_box("Upload / indexing failed", e)

        st.divider()

        # Document tools + index info
        render_sidebar(client)

    # -------------------------
    # Main page
    # -------------------------

    if not ui.index_id:
        info_banner(
            "No document yet",
            "Upload a PDF from the sidebar to get started.",
        )

    tabs = st.tabs(["Chat", "Document"])

    with tabs[0]:
        render_chat(client)

    with tabs[1]:
        render_pdf_panel(client)