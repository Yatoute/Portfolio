# src/chatpdf/ui/utils/caching.py
from __future__ import annotations
import streamlit as st
from chatpdf.ui.api.client import ChatPDFClient

@st.cache_data(show_spinner=False, ttl=60)
def cached_get_index(base_url: str, index_id: str) -> dict:
    client = ChatPDFClient(base_url)
    return client.get_index(index_id)

@st.cache_data(show_spinner=False, ttl=30)
def cached_get_messages(base_url: str, session_id: str) -> dict:
    client = ChatPDFClient(base_url)
    return client.get_messages(session_id)

@st.cache_data(show_spinner=False, ttl=300)
def cached_get_pdf_bytes(base_url: str, index_id: str) -> bytes:
    client = ChatPDFClient(base_url)
    return client.get_pdf(index_id)

def bust_messages_cache():
    cached_get_messages.clear()

def bust_pdf_cache():
    cached_get_pdf_bytes.clear()

def bust_index_cache():
    cached_get_index.clear()