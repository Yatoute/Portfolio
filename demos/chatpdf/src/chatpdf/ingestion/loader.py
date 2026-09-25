from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Optional, Union, List, Dict, Any

from langchain_core.documents import Document
from langchain_community.document_loaders import PyPDFLoader
from langchain_community.document_loaders.parsers import RapidOCRBlobParser

from chatpdf.utils.pipeline import Pipeable

PdfInput = Union[str, Path, bytes, IO[bytes]]

@dataclass(frozen=True)
class PDFLoadOptions:
    """Options de chargement PDF."""
    start_page: int = 0 
    max_pages: Optional[int] = None
    add_source_metadata: bool = True
    extract_images: bool = False

class PDFLoader(Pipeable):
    """
    Service OOP pour charger un PDF et retourner une liste de Documents 
    LangChain.
    - 1 Document par page
    - metadata enrichie pour faciliter citations + traçabilité
    """

    def __init__(
        self, 
        load_options: Optional[PDFLoadOptions] = None
    ) -> None:
        
        self.load_options = load_options or PDFLoadOptions()
        self.images_parser = RapidOCRBlobParser() 
    
    def __call__(
        self, 
        input_: PdfInput, 
        *, 
        file_name: Optional[str] = None,
        load_options: Optional[PDFLoadOptions] = None
    ) -> List[Document]:
        
        return self.load(input_, file_name=file_name, load_options=load_options)

    def load(
        self, 
        input_: PdfInput, 
        *, 
        file_name: Optional[str] = None,
        load_options: Optional[PDFLoadOptions] = None
    ) -> List[Document]:
        """
        Charge un PDF depuis un chemin, bytes ou file-like.

        Args:
            input_: str|Path|bytes|IO[bytes]
            file_name: nom logique du fichier (utile si input_ = bytes/file-like)

        Returns:
            List[Document]
        """
        tmp_path: Optional[str] = None
        
        if not isinstance(load_options, PDFLoadOptions):
            load_options = self.load_options

        try:
            pdf_path, inferred_name, tmp_path = (
                self._normalize_input(input_, file_name=file_name)
            )
            docs = PyPDFLoader(
                file_path=pdf_path,
                extract_images=load_options.extract_images,
                images_parser=self.images_parser if load_options.extract_images else None,
            ).load()
            docs = self._slice_pages(docs, load_options)
            self._enrich_metadata(
                docs=docs, 
                source_path=pdf_path, 
                file_name=inferred_name
            )
            
            return docs

        finally:
            if tmp_path:
                self._safe_remove(tmp_path)
    
    # -------------------------
    # Internals
    # -------------------------

    def _normalize_input(
        self, 
        input_: PdfInput, 
        *, 
        file_name: Optional[str]
    ) -> tuple[str, str, Optional[str]]:
        """
        Returns:
            (pdf_path, effective_file_name, tmp_path_to_cleanup)
        """
        if isinstance(input_, (str, Path)):
            p = Path(input_).expanduser().resolve()
            self._validate_path(p)
            return str(p), p.name, None

        if isinstance(input_, bytes):
            effective_name = file_name or "uploaded.pdf"
            tmp = self._write_tmp_pdf(bytes(input_))
            return tmp, effective_name, tmp

        if hasattr(input_, "read"):
            content = input_.read()
            if not isinstance(content, (bytes, bytearray)):
                raise ValueError(
                    "Le file-like doit être ouvert en mode binaire (read() -> bytes)."
                )
            effective_name = file_name or "uploaded.pdf"
            tmp = self._write_tmp_pdf(bytes(content))
            return tmp, effective_name, tmp

        raise TypeError("Type d'entrée PDF non supporté. Utilise str/Path/bytes/IO[bytes].")

    def _validate_path(self, p: Path) -> None:
        if not p.exists():
            raise FileNotFoundError(f"PDF introuvable: {p}")
        if p.is_dir():
            raise IsADirectoryError(f"Chemin PDF invalide (dossier): {p}")
        if p.suffix.lower() != ".pdf":
            raise ValueError(f"Le fichier ne semble pas être un PDF: {p.name}")

    def _slice_pages(self, docs: List[Document], load_options: PDFLoadOptions) -> List[Document]:

        start_page = load_options.start_page
        max_pages = load_options.max_pages
        total_pages = len(docs)
        
        if isinstance(start_page, int):
            if start_page < 0:
                raise ValueError("start_page doit être >= 0")
            
            if start_page >= total_pages:
                return []
        else:
            start_page = 1

        if isinstance(max_pages, int):
            if max_pages <= 0:
                raise ValueError("max_pages doitêtre > 0 si défini")
        else:
            max_pages = total_pages

        start_page -= 1
        return docs[start_page:start_page + max_pages]
    
    def _enrich_metadata(
        self, 
        docs: List[Document], 
        *, 
        source_path: str, 
        file_name: str
    ) -> None:
        
        if not self.load_options.add_source_metadata:
            return

        for d in docs:
            md = d.metadata or {}

            md.setdefault("source", source_path)
            md.setdefault("file_name", file_name)

            # PyPDFLoader fournit souvent md["page"] (0-index)
            page_idx = md.get("page")
            if isinstance(page_idx, int):
                md.setdefault("page_idx", page_idx)
                md.setdefault("page_num", page_idx + 1)

            d.metadata = md

    def _write_tmp_pdf(self, content: bytes) -> str:
        if not content:
            raise ValueError("Contenu PDF vide.")

        fd, tmp_path = tempfile.mkstemp(suffix=".pdf", prefix="chatpdf_")
        os.close(fd)

        with open(tmp_path, "wb") as f:
            f.write(content)

        return tmp_path

    def _safe_remove(self, path: str) -> None:
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
    
    