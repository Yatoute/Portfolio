from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from langchain_core.documents import Document

from chatpdf.utils.pipeline import Pipeable

@dataclass(frozen=True)
class BridgeOptions:
    tail_chars: int = 600     # fin de la page i
    head_chars: int = 400     # début de la page i+1
    enabled: bool = True

class CrossPageBridge(Pipeable):
    def __init__(self, options: Optional[BridgeOptions] = None) -> None:
        self.options = options or BridgeOptions()
    
    def __call__(
        self, 
        input_docs: List[Document], 
        doc_pages_index: Optional[dict] = None,
        **kwargs
    ) -> Tuple[List[Document], Optional[dict]] | List[Document]:
        
        if doc_pages_index :
            return self.add_bridges(input_docs), doc_pages_index
        
        return self.add_bridges(input_docs), doc_pages_index

    def add_bridges(self, input_docs: List[Document]) -> List[Document]:
        """
        Crée et ajoute des docs synthétiques entre pages i et i+1:
        - utile pour récupérer un passage coupé entre pages
        - sans perdre la citation
        """
        
        if not self.options.enabled:
            return input_docs
        
        output_docs: List[Document] = input_docs.copy()
        for i in range(len(input_docs) - 1):
            a = input_docs[i]
            b = input_docs[i + 1]

            a_text = a.page_content or ""
            b_text = b.page_content or ""

            tail = a_text[-self.options.tail_chars :] if self.options.tail_chars > 0 else ""
            head = b_text[: self.options.head_chars] if self.options.head_chars > 0 else ""

            if not tail.strip() or not head.strip():
                continue

            md_a = a.metadata or {}
            md_b = b.metadata or {}

            file_name = md_a.get("file_name") or md_b.get("file_name")
            source = md_a.get("source") or md_b.get("source")

            page_a = md_a.get("page_num", md_a.get("page"))
            page_b = md_b.get("page_num", md_b.get("page"))

            bridge_md = {
                "source": source,
                "file_name": file_name,
                "is_bridge": True,
                "page_start": page_a,
                "page_end": page_b,
            }

            bridge_doc = Document(
                    page_content=f"{tail}\n\n{head}",
                    metadata=bridge_md,
            )
            output_docs.insert(i + 1, bridge_doc)
            
        return output_docs