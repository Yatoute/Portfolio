FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIDEOMINING_STORAGE_DIR=/data/storage

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 tini \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Single resolution pass: torch is left unpinned in requirements.txt so pip
# can pick whatever version whisperx's `torch~=2.8.0` constraint actually
# needs, while the CPU index keeps it from grabbing the multi-GB CUDA wheel.
RUN pip install --no-cache-dir -r requirements.txt --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple

COPY app.py jobs.py ./
COPY pipeline ./pipeline
COPY templates ./templates
COPY static ./static

RUN mkdir -p /data/storage
VOLUME ["/data/storage"]

EXPOSE 8080

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
