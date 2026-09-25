# indexing.py

from __future__ import annotations

from typing import List, Dict, Any, Tuple, Optional
from langchain_core.documents import Document

from chatpdf.utils.pipeline import Pipeable


class PageIndexer(Pipeable):
    """
    Build a page-level index from Documents.
    """

    def __call__(
        self,
        docs: List[Document],
        **kwargs
    ) -> Tuple[List[Document], Optional[Dict[str, Dict[str, Any]]]]:
        
        doc_pages_index = self.build_pages_index(docs)
        return docs, doc_pages_index

    def build_pages_index(
        self,
        docs: List[Document]
    ) -> Dict[str, Dict[str, Any]]:
        
        index: Dict[str, Dict[str, Any]] = {}

        for doc in docs:
            md = doc.metadata or {}

            doc_id = md.get("file_name", "")
            page_num = md.get("page_num", "")
                
            if doc_id is None or page_num is None:
                continue

            doc_id = str(doc_id)
            page_key = str(page_num)

            index.setdefault(doc_id, {})
            index[doc_id][page_key] = doc.page_content

        return index