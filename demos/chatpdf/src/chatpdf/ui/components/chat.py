from __future__ import annotations

import time
import hashlib
import streamlit as st

from chatpdf.ui.utils.formatings import extract_source_page_entries, source_files_to_text
from chatpdf.ui.caching import cached_get_messages, bust_messages_cache, cached_get_pdf_bytes
from chatpdf.ui.components.page_viewer import render_page_viewer, get_pdf_page_count

from chatpdf.ui.api.client import ChatPDFClient
from chatpdf.ui.api.errors import ApiError

from chatpdf.ui.flows.session import ensure_session, reset_session
from chatpdf.ui.components.notifications import error_box, success_toast, info_banner

@st.fragment
def _render_sources_fragment(
    *,
    sources: list[dict],
    index_id: str,
    api_base_url: str,
    message_key: str,
):
    page_entries = extract_source_page_entries(sources)
    file_names = source_files_to_text(sources)

    if file_names:
        st.caption(f"Files: {file_names}")

    if not page_entries:
        st.markdown("_No page references available._")
        return

    selected_entry_key = f"selected_source_entry_{message_key}"
    selected_page_key = f"selected_source_page_{message_key}"

    if selected_entry_key not in st.session_state:
        st.session_state[selected_entry_key] = page_entries[0]

    st.caption("Open a cited page:")

    cols = st.columns(min(len(page_entries), 6))
    n_cols = len(cols)

    for i, entry in enumerate(page_entries):
        with cols[i % n_cols]:
            if st.button(
                f"Page {entry['label']}",
                key=f"{message_key}_page_{entry['label']}",
                use_container_width=True,
            ):
                st.session_state[selected_entry_key] = entry
                st.session_state[selected_page_key] = entry["page"]
                st.rerun(scope="fragment")

    selected_entry = st.session_state[selected_entry_key]

    if selected_page_key not in st.session_state:
        st.session_state[selected_page_key] = selected_entry["page"]

    try:
        pdf_bytes = cached_get_pdf_bytes(api_base_url, index_id)
        total_pages = get_pdf_page_count(pdf_bytes)
    except Exception as e:
        error_box("Failed to load cited page", e)
        return

    current_page = st.session_state[selected_page_key]

    # Keep original label only if we are still on the initial cited page
    if current_page == selected_entry["page"]:
        current_label = selected_entry["label"]
    else:
        current_label = str(current_page)

    render_page_viewer(
        pdf_bytes=pdf_bytes,
        selected_page=current_page,
        total_pages=total_pages,
        state_key=selected_page_key,
        label=current_label,
        dpi=220,
        show_go_to_page=False,
        fragment_scoped=True,
    )


def _render_message(
    role: str,
    content: str,
    sources: list[dict] | None = None,
    *,
    index_id: str | None = None,
    api_base_url: str | None = None,
    message_key: str,
):
    with st.chat_message("assistant" if role == "assistant" else "user"):
        st.markdown(content)

        if role == "assistant" and sources:
            with st.expander("Sources", expanded=False):
                if index_id and api_base_url:
                    _render_sources_fragment(
                        sources=sources,
                        index_id=index_id,
                        api_base_url=api_base_url,
                        message_key=message_key,
                    )
                else:
                    st.markdown("_Document preview unavailable._")


def render_chat(client: ChatPDFClient):
    ui = st.session_state.ui

    if "pending_prompt" not in st.session_state:
        st.session_state.pending_prompt = None
    if "is_querying" not in st.session_state:
        st.session_state.is_querying = False

    st.markdown("### Chat")

    if not ui.index_id:
        info_banner("Ready when you are", "Upload a PDF, then ask questions about it here.")
        return

    try:
        ensure_session(client)
    except ApiError as e:
        error_box("Could not create chat session", e)
        return

    if not ui.session_id:
        return

    toolbar = st.columns([1, 1, 3])

    with toolbar[0]:
        if st.button("Refresh", use_container_width=True, disabled=st.session_state.is_querying):
            bust_messages_cache()
            st.rerun()

    with toolbar[1]:
        if st.button("Reset chat", use_container_width=True, disabled=st.session_state.is_querying):
            try:
                reset_session(client)
                success_toast("Chat cleared")
            except Exception as e:
                error_box("Reset error", e)
            st.rerun()

    try:
        payload = cached_get_messages(ui.api_base_url, ui.session_id)
    except ApiError as e:
        error_box("Failed to load chat history", e)
        return

    messages = payload.get("messages", [])

    for idx, m in enumerate(messages):
        _render_message(
            m.get("role", "user"),
            m.get("content", ""),
            sources=m.get("sources"),
            index_id=ui.index_id,
            api_base_url=ui.api_base_url,
            message_key=f"history_{idx}",
        )

    pending = st.session_state.pending_prompt
    if pending and not st.session_state.is_querying:
        _render_message(
            "user",
            pending,
            index_id=ui.index_id,
            api_base_url=ui.api_base_url,
            message_key="pending_user",
        )

        st.session_state.is_querying = True
        result = None

        try:
            with st.chat_message("assistant"):
                
                answer_placeholder = st.empty()
                
                with st.spinner("Querying the document..."):
                    tool_placeholder = st.empty()
                    tool_msgs = []
                    for event in client.stream_message(ui.session_id, pending):
                        if event["type"] == "tool":
                            tool_msgs.append(event["content"])
                            tool_placeholder.caption("\n\n".join(tool_msgs))
                        elif event["type"] == "result":
                            result = event["payload"]

                    tool_placeholder.empty()
                    
                    if isinstance(result, dict):
                        content = result.get("content") or ""
                        sources = result.get("sources") or []
                        message_key = f"assistant_{hashlib.md5(content.encode()).hexdigest()[:8]}"

                        def stream_content():
                            for chunk in content.split(" "):
                                yield chunk + " "
                                time.sleep(0.01)

                        answer_placeholder.write_stream(stream_content)

                        if sources:
                            time.sleep(0.01)
                            with st.expander("Sources", expanded=False):
                                if ui.index_id and ui.api_base_url:
                                    _render_sources_fragment(
                                        sources=sources,
                                        index_id=ui.index_id,
                                        api_base_url=ui.api_base_url,
                                        message_key=message_key,
                                    )
                                else:
                                    st.markdown("_Document preview unavailable._")

            bust_messages_cache()

        except ApiError as e:
            error_box("API error", e)
        except Exception as e:
            error_box("Unexpected error", e)
        finally:
            st.session_state.pending_prompt = None
            st.session_state.is_querying = False

        st.rerun()

    prompt = st.chat_input(
        "Ask anything about the document",
        disabled=st.session_state.is_querying,
    )

    if prompt:
        st.session_state.pending_prompt = prompt
        st.rerun()