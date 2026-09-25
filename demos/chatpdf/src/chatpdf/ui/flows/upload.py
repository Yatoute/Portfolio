from __future__ import annotations

import streamlit as st

from chatpdf.ui.api import ChatPDFClient
from chatpdf.ui.state import init_state

def run_upload(client: ChatPDFClient, file, extract_images: bool, start_page, max_pages):
    ui = init_state()
    with st.spinner("Indexation du PDF en cours..."):
        res = client.create_document(
            file_bytes=file.getvalue(),
            filename=file.name,
            extract_images=extract_images,
            start_page=start_page,
            max_pages=max_pages,
        )
    ui.doc_id = res["doc_id"]
    ui.index_id = res["index_id"]
    ui.file_name = res["file_name"]
    ui.total_pages = res["total_pages"]
    
    # Sync index_id vers le heartbeat de cette session
    hb = st.session_state.get("heartbeat")
    if hb:
        hb.index_id = ui.index_id
        
    return res