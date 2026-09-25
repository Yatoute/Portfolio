"""
Seeds storage/ with a bundled example PDF on first boot, so a public demo
is immediately usable without requiring a visitor to upload anything.

The example is rebuilt from scratch (ingestion + embeddings) against the
runtime storage_dir, rather than copying a pre-built FAISS index — a
pre-built index bundled from a dev machine embeds absolute paths (including
inside the pickled FAISS docstore) that would not resolve inside the
container.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from chatpdf.api.config import settings

logger = logging.getLogger(__name__)

# Bundled inside the Docker image (see Dockerfile `COPY examples/preload`).
# NOT derived from __file__: once installed as a wheel (`pip install .`),
# the package lives under site-packages, several directories away from the
# project root that actually contains examples/ — climbing parents() from
# __file__ silently resolved to a bogus path in production
# (/usr/local/lib/python3.12/examples/preload) instead of /app/examples/preload.
EXAMPLE_SOURCE_DIR = Path(os.environ.get("CHATPDF_EXAMPLE_DIR", "/app/examples/preload"))
DEFAULT_INDEX_FILENAME = "default_index_id.txt"


def _write_default_index_id(index_id: str) -> None:
    (settings.storage_dir / DEFAULT_INDEX_FILENAME).write_text(index_id, encoding="utf-8")


def get_default_index_id() -> str | None:
    path = settings.storage_dir / DEFAULT_INDEX_FILENAME
    if not path.exists():
        return None
    index_id = path.read_text(encoding="utf-8").strip()
    return index_id or None


def maybe_preload_example() -> None:
    """Idempotent: no-ops if storage already has at least one index."""
    indexes_dir = settings.indexes_dir()
    if any(indexes_dir.iterdir()):
        logger.info("Preload skipped: storage already has indexes.")
        return

    pdfs = sorted(EXAMPLE_SOURCE_DIR.glob("*.pdf"))
    if not pdfs:
        logger.warning("Preload skipped: no example PDF bundled at %s", EXAMPLE_SOURCE_DIR)
        return

    example_pdf = pdfs[0]
    logger.info("Preloading example document: %s", example_pdf.name)

    # Imported lazily to avoid a circular import (routers imports this module).
    from chatpdf.api.services import ChatPDFService
    from chatpdf.utils import clean_filename
    import uuid

    uploads_dir = settings.uploads_dir()
    name, ext = example_pdf.stem, example_pdf.suffix
    safe_name = f"{clean_filename(name)}-{uuid.uuid4().hex[:12]}{ext}"
    dest = uploads_dir / safe_name
    dest.write_bytes(example_pdf.read_bytes())

    service = ChatPDFService()
    try:
        meta = service.create_index_from_upload(
            uploaded_pdf_path=dest,
            file_name=safe_name,
            extract_images=True,
        )
    except Exception:
        logger.exception("Preload failed while indexing the example document.")
        return

    _write_default_index_id(meta.index_id)
    logger.info("Preload complete: index_id=%s", meta.index_id)
