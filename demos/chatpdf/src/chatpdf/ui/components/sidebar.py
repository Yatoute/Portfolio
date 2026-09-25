from __future__ import annotations

import time
import streamlit as st

from chatpdf.ui.api.client import ChatPDFClient
from chatpdf.ui.api.errors import ApiError
from chatpdf.ui.caching import (
    cached_get_index,
    cached_get_pdf_bytes,
    bust_pdf_cache,
    bust_index_cache,
)
from chatpdf.ui.components.notifications import error_box, info_banner, success_toast
from chatpdf.ui.static import sidebar_footer

def render_sidebar(client: ChatPDFClient):
    """
    Sidebar content (document actions + metadata).
    IMPORTANT: this function must NOT use `with st.sidebar:`
    so that the caller controls ordering.
    """
    ui = st.session_state.get("ui")
    
    if ui is None:
        sidebar_footer()
        return

    #st.markdown("### Document tools")
    time.sleep(0.1)
    if ui.index_id:
        
        with st.expander("Index info", expanded=False):
            try:
                index_meta = cached_get_index(ui.api_base_url, ui.index_id)
                st.json(index_meta)
            except ApiError as e:
                error_box("Failed to load index metadata", e)

        cols = st.columns(2)
        with cols[0]:
            if st.button("Reload PDF", use_container_width=True):
                bust_pdf_cache()
                success_toast("PDF cache cleared")
                st.rerun()

        with cols[1]:
            if st.button("Reload index", use_container_width=True):
                bust_index_cache()
                success_toast("Index cache cleared")
                st.rerun()

        try:
            pdf_bytes = cached_get_pdf_bytes(ui.api_base_url, ui.index_id)
            fname = (ui.file_name or f"{ui.index_id}.pdf").replace(" ", "_")
            st.download_button(
                label="Download PDF",
                data=pdf_bytes,
                file_name=fname,
                mime="application/pdf",
                use_container_width=True,
            )
        except ApiError as e:
            error_box("Failed to fetch PDF", e)
    else:
        info_banner("No document loaded", "Upload a PDF to enable document actions.")
        # sidebar_footer()
        # return
    
    st.divider()
    sidebar_footer()

