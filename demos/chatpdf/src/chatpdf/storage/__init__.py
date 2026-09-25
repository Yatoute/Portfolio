from __future__ import annotations

from chatpdf.storage.faiss_store import FaissStore
from chatpdf.storage.page_index import PageIndex

__all__ = [
    "FaissStore",
    "PageIndex",
    "DocumentRepository"
]

class DocumentRepository:
    def __init__(self, vector_store: FaissStore, page_index: PageIndex):
        self.vector_store = vector_store
        self.page_index = page_index
