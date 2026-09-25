from __future__ import annotations

import streamlit as st

def info_banner(title: str, body: str):
    st.info(f"**{title}**\n\n{body}")

def error_box(title: str, err: Exception):
    st.error(f"**{title}**\n\n{err}")

def success_toast(msg: str):
    try:
        st.toast(msg, icon="✅")
    except Exception:
        st.success(msg)