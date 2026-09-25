from __future__ import annotations

from typing import List, Optional

from langchain_core.documents import Document
from langchain_core.tools import tool

from chatpdf.storage import DocumentRepository
from chatpdf.agent.schemas import *

class ChatPDFTools:
    
    _TOOL_MESSAGES = {
        "semantic_search": lambda args: f"🔎 Searching the document for *\"{args.get('query')}\"* to find relevant evidence...",
        "fetch_next": lambda args: "📖 The explanation seems to continue — fetching the next chunk for more context...",
        "fetch_previous": lambda args: "📖 The idea may have started earlier — fetching the previous chunk to get the full picture...",
        "fetch_window": lambda args: "📑 The context looks incomplete — fetching surrounding chunks to get a broader view...",
        "fetch_page": lambda args: f"📄 The answer may be on page(s) {args.get('page_numbers')} — retrieving full page content from *{args.get('file_name')}* for more detail...",
        "list_documents": lambda args: "📂 Checking which documents are available in this session...",
    }
        
    def __init__(self, document_repository: DocumentRepository ) -> None:
        self.vector_store = document_repository.vector_store
        self.page_index = document_repository.page_index

        
    # ---- packing ----

    def _to_chunk_view(self, doc: Document, max_chars: int = 1200) -> ChunkView:
        md = doc.metadata or {}
        text = (doc.page_content or "").strip()

        if max_chars and len(text) > max_chars:
            text = text[:max_chars] + "…"

        pages: Optional[str] = None
        if md.get("is_bridge") and md.get("page_start") and md.get("page_end"):
            pages = f"{md['page_start']}-{md['page_end']}"
        elif md.get("page_num"):
            pages = str(md["page_num"])

        return ChunkView(
            chunk_id=str(md.get("chunk_id", "")),
            text=text,
            file_name=md.get("file_name"),
            pages=pages,
        )

    def _to_chunk_views(self, docs: List[Document], max_chars: int = 1200) -> List[ChunkView]:
        return [self._to_chunk_view(d, max_chars=max_chars) for d in docs]

    # ---- tool builders ----

    def build(self):
        vs = self.vector_store
        pi = self.page_index

        @tool(args_schema=SemanticSearchArgs)
        def semantic_search(
            query: str,
            k: int = 5,
            search_type: str = "mmr"
        ) -> List[ChunkView]:
            """
            Retrieve the most relevant PDF chunks for a query.

            - Use `search_type="mmr"` to get diverse but relevant chunks.
            - Use `search_type="similarity"` if results are off-topic and you need stricter relevance.
            """
            retriever = vs.as_retriever(k=k, search_type=search_type)
            docs = retriever.invoke(query)
            return self._to_chunk_views(docs)

        @tool(args_schema=ChunkIdArgs)
        def fetch_next(chunk_id: str) -> Optional[ChunkView]:
            """
            Fetch the next chunk in the same document.

            Use this when you need the continuation of an idea that starts in the current chunk
            """
            doc = vs.fetch_next(chunk_id=chunk_id)
            return self._to_chunk_view(doc) if doc else None

        @tool(args_schema=ChunkIdArgs)
        def fetch_previous(chunk_id: str) -> Optional[ChunkView]:
            """
            Fetch the previous chunk in the same document.

            Use this when you need the beginning of an idea that starts before the current chunk.
            """
            doc = vs.fetch_previous(chunk_id=chunk_id)
            return self._to_chunk_view(doc) if doc else None

        @tool(args_schema=WindowArgs)
        def fetch_window(
            chunk_id: str,
            n_before: int = 1,
            n_after: int = 2
        ) -> List[ChunkView]:
            """
            Fetch a window of neighbor chunks around a given chunk_id.

            Use this when the idea likely spans multiple adjacent chunks and you need full surrounding context.
            """
            docs = vs.fetch_window(chunk_id=chunk_id, n_before=n_before, n_after=n_after)
            return self._to_chunk_views(docs)

        @tool(args_schema=FetchPageArgs)
        def fetch_page(
            file_name: str,
            page_numbers: List[int]
        ) -> List[PageView]:
            """
            Retrieve the content of a specific page or list of pages for a given document.

            Use this when you need specific page content, OR topic details referenced in the context.
            """
            
            result = []
            for page_number in page_numbers:
                page_content = pi.get_page(file_name, page_number)
                
                if not page_content:
                    page_content = f"Page {page_number} not found in document {file_name}."
                
                result.append(
                    PageView(
                        file_name=file_name,
                        page_number=page_number,
                        content=page_content
                    )
                )
            return result

        @tool()
        def list_documents() -> List[DocumentView]:
            """
            List all documents file_names available in the user session.
            """
            list_document_names = pi.list_documents()
            return [DocumentView(file_name=name) for name in list_document_names]

        return [
            semantic_search,
            fetch_page,
            fetch_next,
            fetch_previous,
            fetch_window,
            list_documents,
        ]

    @classmethod
    def get_tool_message(cls, name: str, args: dict) -> str:
        handler = cls._TOOL_MESSAGES.get(name)
        if handler:
            return handler(args)
        return f"🔧 Calling `{name}`..."

# ---- exports ----
get_tool_message = ChatPDFTools.get_tool_message    