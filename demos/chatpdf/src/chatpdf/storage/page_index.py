from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional, Any, Union

from chatpdf.utils.pipeline import Pipeline

class PageIndex:
    """
    Central storage for full-page PDF text content.

    Can be instantiated:
    - from an existing Python dict (doc_pages_index)
    - from a JSON file
    - from a PDF file using an ingestion pipeline
    """

    def __init__(self, index: Optional[Dict[str, Dict[str, str]]] = None):
        """
        Initialize PageIndex with an optional pre-built dictionary.
        """
        self._index: Dict[str, Dict[str, str]] = index or {}

    # ------------------------------------------------------------------
    # Construction Methods
    # ------------------------------------------------------------------

    @classmethod
    def from_json_index(cls, json_path: Union[str, Path]) -> "PageIndex":
        """
        Load a PageIndex from a JSON file PageIndex.
        """
        path = Path(json_path)

        if not path.exists():
            raise FileNotFoundError(f"Index file not found: {json_path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return cls(index=data)

    @classmethod
    def from_pdf(
        cls,
        pdf_path: Union[str, Path],
        pipeline: Pipeline,
        **pipeline_kwargs: Any,
    ) -> "PageIndex":
        """
        Build PageIndex from a PDF using your ingestion pipeline.

        Example:
            pipeline = PDFLoader() | TextCleaner() | PageIndexer()
            page_index = PageIndex.from_pdf("file.pdf", pipeline)
        """
        _, doc_pages_index = pipeline(pdf_path, **pipeline_kwargs)

        return cls(index=doc_pages_index)

    # ------------------------------------------------------------------
    # Access Methods
    # ------------------------------------------------------------------

    def get_page(self, doc_id: str, page_number: Union[int, str]) -> Optional[str]:
        """
        Retrieve text of a specific page.
        """
        page_number = str(page_number)
        return self._index.get(doc_id, {}).get(page_number)

    def get_document_pages(self, doc_id: str) -> Optional[Dict[str, str]]:
        """
        Retrieve all pages for a given document.
        """
        return self._index.get(doc_id)

    def list_documents(self) -> list[str]:
        """
        Return list of indexed document IDs.
        """
        return list(self._index.keys())

    def has_document(self, doc_id: str) -> bool:
        """
        Check if a document exists in index.
        """
        return doc_id in self._index

    def has_page(self, doc_id: str, page_number: Union[int, str]) -> bool:
        """
        Check if a page exists in index.
        """
        page_number = str(page_number)
        return page_number in self._index.get(doc_id, {})

    # ------------------------------------------------------------------
    # Modification Methods
    # ------------------------------------------------------------------

    def add_document(self, doc_id: str, pages: Dict[Union[int, str], str]) -> None:
        """
        Add or replace a full document.
        """
        self._index[doc_id] = {str(k): v for k, v in pages.items()}

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def save(self, output_path: Union[str, Path]) -> None:
        """
        Save index to JSON file.
        """
        path = Path(output_path)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._index, f, indent=2, ensure_ascii=False)

    def to_dict(self) -> Dict[str, Dict[str, str]]:
        """
        Return raw dictionary representation.
        """
        return self._index

    # ------------------------------------------------------------------
    # Dunder Methods
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        """
        Number of documents indexed.
        """
        return len(self._index)

    def __repr__(self) -> str:
        return f"PageIndex(num_documents={len(self)})"