from __future__ import annotations

from typing import Optional, Literal, List, Union, Dict, Any
from pydantic import BaseModel, Field

import re


PageRef = Union[int, str]

class Citation(BaseModel):
    file_name: str = Field(
        ...,
        description="Name of the PDF file where the evidence was found."
    )

    pages: List[PageRef] = Field(
        ...,
        description=(
            "List of page references. "
            "Each item must be either an integer (single page) "
            "or a string representing a page range formatted as 'start-end'. "
            "Example: [25, '26-28', 30]"
        )
    )

class ChatPDFOutput(BaseModel):
    answer: str = Field(
        ...,
        description="Your final answer."
    )

    citations: List[Citation] = Field(
        ...,
        description=(
            "Citations, grouped by document. "
            "Return at most ONE Citation per file_name. "
            "If multiple pages are used from the same file, merge them into the same Citation. "
            "pages must contain UNIQUE page numbers (no duplicates) sorted ascending."
        )
    )

class DocumentView(BaseModel):
    file_name: str = Field(..., description="File name of the source PDF document.")

class PageView(BaseModel):
    file_name: str = Field(..., description="File name of the source PDF document.")
    page_number: int = Field(..., description="Page number within the document.")
    content: str = Field(..., description="Text content of the specified page.")
    
class ChunkView(BaseModel):
    chunk_id: str = Field(..., description="Unique chunk identifier to fetch neighbors later.")
    text: str = Field(..., description="Chunk textual content (page_content).")
    file_name: Optional[str] = Field(None, description="Source PDF file name.")
    pages: Optional[str] = Field(
        None,
        description="Page info: '7' or '2-3' for bridge chunks.",
    )

class SemanticSearchArgs(BaseModel):
    query: str = Field(..., description="User question or refined query for retrieval.")
    k: int = Field(5, ge=1, le=10, description="Number of documents to return.")
    search_type: Literal["mmr", "similarity"] = Field(
        "mmr",
        description="Retrieval strategy. Use mmr for diversity, similarity for strict relevance.",
    )

class ChunkIdArgs(BaseModel):
    chunk_id: str = Field(..., description="Chunk identifier (ChunkView().chunk_id).")

class WindowArgs(BaseModel):
    chunk_id: str = Field(..., description="Chunk identifier (ChunkView().chunk_id).")
    n_before: int = Field(1, ge=0, le=1, description="How many previous chunks to fetch.")
    n_after: int = Field(2, ge=0, le=4, description="How many next chunks to fetch.")

class FetchPageArgs(BaseModel):
    file_name: str = Field(..., description="File name of the source PDF document.")
    page_numbers: List[int] = Field(..., description="List of page numbers to fetch.")
