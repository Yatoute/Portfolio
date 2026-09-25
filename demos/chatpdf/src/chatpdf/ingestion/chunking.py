from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import List, Tuple, Optional

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from chatpdf.utils.pipeline import Pipeable


@dataclass(frozen=True)
class ChunkingOptions:
    chunk_size: int = 1000
    chunk_overlap: int = 200
    separators: Tuple[str, ...] = ("\n\n", "\n", ". ", " ", "")
    enrich_graph: bool = True 

class DocumentsChunker(Pipeable):
    """
    Documents chunker (OOP)".

    Ajoute dans metadata:
    - chunk_index (global)
    - chunk_id (stable)
    - prev_chunk_id / next_chunk_id (dans le même doc_id)
    """

    def __init__(self, options: Optional[ChunkingOptions] = None) -> None:
        self.options = options or ChunkingOptions()
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.options.chunk_size,
            chunk_overlap=self.options.chunk_overlap,
            separators=list(self.options.separators),
            add_start_index=True,  # -> metadata["start_index"]
        )

    def __call__(
        self, 
        input_docs: List[Document],
        doc_pages_index: Optional[dict] = None, 
        **kwargs
    ) -> Tuple[List[Document], Optional[dict]] | List[Document]:
        if doc_pages_index :
            return self.chunk(input_docs), doc_pages_index
        
        return self.chunk(input_docs)

    def chunk(self, docs: List[Document]) -> List[Document]:
        chunks = self._splitter.split_documents(docs)

        if self.options.enrich_graph:
            self._enrich_chunks_with_graph(chunks)

        return chunks

    # -------------------------
    # Internals
    # -------------------------

    def _enrich_chunks_with_graph(
        self, 
        chunks: list[Document]
    ) -> None:
        """
        Ajoute:
        - chunk_id
        - chunk_index
        - prev_chunk_id
        - next_chunk_id
        """

        # assign chunk_index
        for i, doc in enumerate(chunks):
            md = doc.metadata or {}
            md["chunk_index"] = i
            doc.metadata = md

        # assign chunk_id stable
        for doc in chunks:
            md = doc.metadata
            raw = f"{md.get('file_name')}|{md.get('chunk_index')}|{doc.page_content[:50]}"
            md["chunk_id"] = hashlib.sha1(raw.encode()).hexdigest()
            doc.metadata = md
            
        # link neighbors
        for i, doc in enumerate(chunks):
            md = doc.metadata
            if i > 0:
                md["prev_chunk_id"] = chunks[i - 1].metadata["chunk_id"]
            if i < len(chunks) - 1:
                md["next_chunk_id"] = chunks[i + 1].metadata["chunk_id"]
            doc.metadata = md