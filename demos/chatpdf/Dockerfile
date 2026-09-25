FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System dependencies. libgl1/libglib2.0-0/libxcb1/libxext6/libsm6/libxrender1
# are needed by opencv-python (a rapidocr dependency, used for OCR) — the GUI
# build of opencv, not opencv-python-headless, needs these X11 client libs
# even though nothing here ever opens a display.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      curl tini nginx gettext-base \
      libgl1 libglib2.0-0 libxcb1 libxext6 libsm6 libxrender1 \
 && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml README* ./
COPY src ./src
COPY examples/preload ./examples/preload

# Install Python dependencies
RUN python -m pip install --upgrade --no-cache-dir pip setuptools wheel \
 && python -m pip install --no-cache-dir ".[api,ui]" \
 && python -m pip install --no-cache-dir supervisor

# Runtime configuration
ENV CHATPDF_API_HOST=127.0.0.1 \
    CHATPDF_API_PORT=8000 \
    CHATPDF_UI_HOST=127.0.0.1 \
    CHATPDF_UI_PORT=8501 \
    CHATPDF_STORAGE_DIR=/data/storage \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ENABLECORS=false \
    STREAMLIT_BROWSER_GATHERUSAGESTATS=false

# Mount point for the Fly.io persistent volume (uploaded PDFs + FAISS indexes)
RUN mkdir -p /data/storage
VOLUME ["/data/storage"]

# Nginx configuration
COPY docker/nginx.conf.template /etc/nginx/templates/nginx.conf.template

# Supervisor configuration
COPY docker/supervisord.conf /etc/supervisord.conf

# Entrypoint
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Ports
EXPOSE 8000 8501

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/entrypoint.sh"]