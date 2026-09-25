from __future__ import annotations

import fitz
import streamlit as st


@st.cache_data(show_spinner=False, ttl=300)
def render_page_png(pdf_bytes: bytes, page_index: int, dpi: int = 220) -> bytes:
    """
    Render a single PDF page to PNG bytes.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(page_index)
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    return pix.tobytes("png")


@st.cache_data(show_spinner=False, ttl=300)
def get_pdf_page_count(pdf_bytes: bytes) -> int:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    return doc.page_count

def render_page_viewer(
    *,
    pdf_bytes: bytes,
    selected_page: int,
    total_pages: int,
    state_key: str,
    label: str | None = None,
    dpi: int = 220,
    show_go_to_page: bool = False,
    fragment_scoped: bool = False,
):
    """
    Reusable page viewer.

    Parameters
    ----------
    pdf_bytes:
        PDF content as bytes.
    selected_page:
        Current page (1-based).
    total_pages:
        Total number of pages in the PDF.
    state_key:
        Session state key used to store current page.
    label:
        Display label. Example: "15-20" or "16".
    dpi:
        Render quality.
    show_go_to_page:
        If True, show "Go to page" input.
    fragment_scoped:
        If True, use st.rerun(scope="fragment") for navigation.
    """
    selected_page = max(1, min(selected_page, total_pages))
    st.session_state[state_key] = selected_page

    display_label = label or str(selected_page)
    goto_key = f"{state_key}_goto_input"
    sync_goto_key = f"{state_key}_sync_goto"

    # Synchronize the number_input value BEFORE the widget is instantiated
    if show_go_to_page:
        if goto_key not in st.session_state:
            st.session_state[goto_key] = selected_page
        elif st.session_state.get(sync_goto_key, False):
            st.session_state[goto_key] = selected_page
            st.session_state[sync_goto_key] = False

    try:
        png_bytes = render_page_png(pdf_bytes, page_index=selected_page - 1, dpi=dpi)
    except Exception as e:
        st.error(f"Failed to render page: {e}")
        return

    if show_go_to_page:
        top_cols = st.columns([1, 2, 1])

        with top_cols[1]:
            goto = st.number_input(
                "Go to page",
                min_value=1,
                max_value=total_pages,
                step=1,
                key=goto_key,
                label_visibility="hidden",
            )

            if goto != selected_page:
                st.session_state[state_key] = int(goto)
                st.session_state[sync_goto_key] = False
                if fragment_scoped:
                    st.rerun(scope="fragment")
                st.rerun()

    st.caption(f"Preview — Page {display_label} / {total_pages}")
    st.image(png_bytes, width="stretch")

    bottom_cols = st.columns([1, 2, 1])

    with bottom_cols[0]:
        if st.button(
            "◀ Previous",
            key=f"{state_key}_bottom_prev",
            width="stretch",
            disabled=(selected_page <= 1),
        ):
            new_page = selected_page - 1
            st.session_state[state_key] = new_page
            st.session_state[sync_goto_key] = True
            if fragment_scoped:
                st.rerun(scope="fragment")
            st.rerun()

    with bottom_cols[1]:
        st.markdown(
            f"<div style='text-align:center; padding-top:0.4rem;'>Page {display_label}</div>",
            unsafe_allow_html=True,
        )

    with bottom_cols[2]:
        if st.button(
            "Next ▶",
            key=f"{state_key}_bottom_next",
            width="stretch",
            disabled=(selected_page >= total_pages),
        ):
            new_page = selected_page + 1
            st.session_state[state_key] = new_page
            st.session_state[sync_goto_key] = True
            if fragment_scoped:
                st.rerun(scope="fragment")
            st.rerun()