from __future__ import annotations

import streamlit as st

from chatpdf.ui.api import ChatPDFClient
from chatpdf.ui.caching import bust_messages_cache

def ensure_session(client: ChatPDFClient) -> str | None:
    """
    Create session if ui.index_id is set and ui.session_id is empty.
    """
    ui = st.session_state.ui
    if not ui.index_id:
        return None

    if ui.session_id:
        return ui.session_id

    with st.spinner("Création de la session de chat..."):
        res = client.create_session(ui.index_id)

    ui.session_id = res["session_id"]
    bust_messages_cache()
    return ui.session_id

def reset_session(client: ChatPDFClient):
    """
    Delete current session and clear it from UI.
    """
    ui = st.session_state.ui
    if not ui.session_id:
        return

    with st.spinner("Suppression de la session..."):
        try:
            client.delete_session(ui.session_id)
        finally:
            ui.session_id = None
            bust_messages_cache()