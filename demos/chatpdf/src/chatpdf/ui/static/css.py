import streamlit as st

def add_ui_css():
    st.markdown(
        """
        <style>
        /* Hide Streamlit multi-page navigation (app / workspace) */
        [data-testid="stSidebarNav"] { display: none !important; }
        section[data-testid="stSidebar"] [data-testid="stSidebarNav"] { display: none !important; }

        /* Increase sidebar width */
        section[data-testid="stSidebar"] { width: 420px !important; }
        section[data-testid="stSidebar"] > div:first-child { width: 420px !important; }

        /* Keep main content aligned */
        @media (min-width: 768px) {
          .stAppViewContainer .main .block-container { padding-left: 2.5rem; padding-right: 2.5rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

def sidebar_footer(url: str = "https://github.com/Yatoute") -> None:
    st.markdown(f"""
        <style>
            .sidebar-footer {{
                position: fixed;
                bottom: 1rem;
                left: 0;
                width: 18rem;
                text-align: center;
                font-size: 0.8rem;
                color: gray;
            }}
            .sidebar-footer a {{
                color: gray;
                text-decoration: none;
            }}
            .sidebar-footer a:hover {{
                text-decoration: underline;
            }}
        </style>
        <div class="sidebar-footer">
            Built by <a href="{url}" target="_blank">@Yatoute</a>
        </div>
    """, unsafe_allow_html=True)