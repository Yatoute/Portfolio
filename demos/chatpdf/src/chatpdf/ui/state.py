from __future__ import annotations

from dataclasses import dataclass
import os
import time
import threading
import requests
import streamlit as st


class _HeartbeatState:
    def __init__(self, api_base_url: str, streamlit_session_id: str):
        self.index_id: str | None = None
        self.api_base_url: str = api_base_url
        self.streamlit_session_id: str = streamlit_session_id
        self._thread: threading.Thread | None = None
        self.heartbeat_every_seconds: int = 15*60

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        def beat():
            from streamlit.runtime import get_instance
            print(f"[HEARTBEAT] thread running — watching session {self.streamlit_session_id}", flush=True)
            while True:
                # sleep
                time.sleep(self.heartbeat_every_seconds)
                
                # Vérifier si la session Streamlit est encore vivante
                try:
                    runtime = get_instance()
                    session_info = runtime._session_mgr.get_session_info(self.streamlit_session_id)
                    if session_info is None:
                        print(f"[HEARTBEAT] session {self.streamlit_session_id} morte — arrêt", flush=True)
                        self.index_id = None
                        return
                except Exception as e:
                    print(f"[HEARTBEAT] erreur session check — arrêt: {e}", flush=True)
                    self.index_id = None
                    return

                index_id = self.index_id
                if index_id:
                    try:
                        resp = requests.post(
                            f"{self.api_base_url.rstrip('/')}/api/v1/heartbeat",
                            json={"index_id": index_id},
                            timeout=3,
                        )
                        print(f"[HEARTBEAT] sent — session={self.streamlit_session_id} status={resp.status_code}", flush=True)
                    except Exception as e:
                        print(f"[HEARTBEAT] failed: {e}", flush=True)
                
        self._thread = threading.Thread(
            target=beat,
            daemon=True,
            name=f"heartbeat-{self.streamlit_session_id[:8]}"
        )
        self._thread.start()
        print(f"[HEARTBEAT] thread started for session {self.streamlit_session_id}", flush=True)


@dataclass
class UiState:
    api_base_url: str = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
    doc_id: str | None = None
    index_id: str | None = None
    session_id: str | None = None
    file_name: str | None = None
    total_pages: int | None = None


def _get_streamlit_session_id() -> str | None:
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    ctx = get_script_run_ctx()
    return ctx.session_id if ctx else None


def _get_or_create_heartbeat() -> _HeartbeatState | None:
    """Retourne le heartbeat lié à la session courante, en le créant si besoin."""
    if "heartbeat" not in st.session_state:
        session_id = _get_streamlit_session_id()
        if not session_id:
            return None
        api_base_url = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
        st.session_state.heartbeat = _HeartbeatState(
            api_base_url=api_base_url,
            streamlit_session_id=session_id,
        )
    return st.session_state.heartbeat


def init_state() -> UiState:
    st.session_state.ui = UiState()

    hb = _get_or_create_heartbeat()
    if hb:
        hb.index_id = None
        hb.start()

    return st.session_state.ui


def ensure_state() -> UiState:
    if "ui" not in st.session_state:
        st.session_state.ui = UiState()

    hb = _get_or_create_heartbeat()
    if hb:
        hb.start()

    return st.session_state.ui