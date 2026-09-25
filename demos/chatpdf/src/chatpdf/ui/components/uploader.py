import streamlit as st

MAX_FILE_SIZE_MB = 50

def render_uploader(ui):
    st.markdown("### Upload a PDF")

    limit_pages = st.toggle("Limit pages to index", value=False)

    with st.form("upload_form", clear_on_submit=False):
        file = st.file_uploader(
            "Choose a PDF file",
            type=["pdf"],
        )
        extract_images = st.toggle("Extract images", value=True)

        start_page = None
        max_pages = None

        if limit_pages:
            cols = st.columns(2)
            with cols[0]:
                start_page = st.number_input("Start page", min_value=1, value=1, step=1)
                start_page = int(start_page)
            with cols[1]:
                max_pages = st.number_input("Max pages", min_value=1, value=10, step=1)
                max_pages = int(max_pages)

        submit = st.form_submit_button("Index document", use_container_width=True)

    # Validate file size
    file_too_large = file is not None and file.size > MAX_FILE_SIZE_MB * 1024 * 1024
    if file_too_large:
        st.error(f"📄 File too large. Please upload a PDF under {MAX_FILE_SIZE_MB} MB.")

    return {
        "file": file,
        "extract_images": extract_images,
        "start_page": start_page,
        "max_pages": max_pages,
        "submit": submit and file is not None and not file_too_large,
    }