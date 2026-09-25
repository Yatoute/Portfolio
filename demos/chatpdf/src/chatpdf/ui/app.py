import time

import streamlit as st

from chatpdf.ui.state import ensure_state
from chatpdf.ui.pages import render_workspace

from chatpdf.ui.static import add_ui_css

def run():
    st.set_page_config(page_title="ChatPDF", page_icon="📄", layout="wide")
    ensure_state()
    add_ui_css()
    #time.sleep(0.5)
    render_workspace()

if __name__ == "__main__":
    run()