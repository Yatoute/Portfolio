from __future__ import annotations

import json
import uuid
import time
import shutil
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from pydantic import ValidationError

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from chatpdf.ingestion import (
    PDFLoader,
    PDFLoadOptions,
    TextCleaner,
    PageIndexer,
    CrossPageBridge,
    DocumentsChunker,
)
from chatpdf.models import ModelsFactory
from chatpdf.storage import FaissStore, PageIndex, DocumentRepository
from chatpdf.agent import ChatPDFAgent, ChatPDFOutput
from chatpdf.utils import hash_file

from chatpdf.api.config import settings
from chatpdf.api.schemas import AssistantResponse, MessageView

INDEX_META_FILENAME = "index_meta.json"
PAGE_INDEX_FILENAME = "doc_pages_index.json"
FAISS_DIRNAME = "faiss_index"


@dataclass
class IndexMeta:
    index_id: str
    doc_id: str
    file_name: str
    created_at: str
    pdf_path: str
    faiss_dir: str
    page_index_json: str
    embedding_provider: Optional[str] = None
    embedding_model: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index_id": self.index_id,
            "doc_id": self.doc_id,
            "file_name": self.file_name,
            "created_at": self.created_at,
            "pdf_path": self.pdf_path,
            "faiss_dir": self.faiss_dir,
            "page_index_json": self.page_index_json,
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
        }

    @classmethod
    def from_path(cls, meta_path: Path) -> "IndexMeta":
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return cls(**data)


@dataclass
class SessionState:
    session_id: str
    index_id: str
    created_at: datetime
    agent: ChatPDFAgent


class ChatPDFService:
    """
    Object-oriented service layer for:
    - Index creation/loading
    - Session lifecycle
    - Chat calls
    - Page access

    Notes:
    - Sessions are stored in-memory (v1). TODO: swap for Redis in Prod.
    """

    def __init__(self, models_factory: Optional[ModelsFactory] = None) -> None:
        self._models_factory = models_factory or ModelsFactory()
        self._sessions: Dict[str, SessionState] = {}
        self._heartbeats: Dict[str, datetime] = {}  # index_id -> last seen
        self._start_cleanup_watchdog()

    def _start_cleanup_watchdog(
        self, 
        check_every_seconds: int = 5*60,
        expired_after_seconds: int = 20*60
        ) -> None:
        """Background thread: cleanup indexes with no heartbeat for > timeout_seconds."""
        import threading

        def watchdog():
            while True:
                time.sleep(check_every_seconds)
                now = datetime.utcnow()
                expired_indexes = [
                    index_id
                    for index_id, last_seen in list(self._heartbeats.items())
                    if (now - last_seen).total_seconds() > expired_after_seconds
                ]
                for index_id in expired_indexes:
                    logging.info(f"Watchdog: no heartbeat for {index_id}, cleaning up.")
                    self._heartbeats.pop(index_id, None)
  
                    self.cleanup_workspace(index_id=index_id)

        thread = threading.Thread(target=watchdog, daemon=True, name="cleanup-watchdog")
        thread.start()

    def record_heartbeat(self, index_id: str) -> None:
        self._heartbeats[index_id] = datetime.utcnow()

    def cancel_heartbeat(self, index_id: str) -> None:
        self._heartbeats.pop(index_id, None)
        
    # ----------------------------
    # Index management
    # ----------------------------

    def create_index_from_upload(
        self,
        *,
        uploaded_pdf_path: Path,
        file_name: str,
        extract_images: bool = True,
        start_page: Optional[int] = None,
        max_pages: Optional[int] = None,
    ) -> IndexMeta:
        """
        One-shot: ingestion -> faiss build -> save -> write index_meta.json
        """
        doc_id = hash_file(uploaded_pdf_path)
        
        index_id = f"idx_{uuid.uuid4().hex[:12]}"
        idx_dir = settings.indexes_dir() / index_id
        idx_dir.mkdir(parents=True, exist_ok=True)

        load_options = PDFLoadOptions(
            extract_images=extract_images,
            start_page=start_page,
            max_pages=max_pages,
        )

        pipeline = PDFLoader() | TextCleaner() | PageIndexer() | CrossPageBridge() | DocumentsChunker()

        docs, doc_pages_index = pipeline(
            str(uploaded_pdf_path),
            file_name=file_name,
            load_options=load_options,
        )

        page_index_path = idx_dir / PAGE_INDEX_FILENAME
        page_index_path.write_text(
            json.dumps(doc_pages_index, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Build FAISS
        embed_model = self._models_factory.embeddings()
        faiss_dir = idx_dir / FAISS_DIRNAME

        store = FaissStore(embeddings=embed_model)
        store.build(docs)
        store.save(faiss_dir)

        meta = IndexMeta(
            index_id=index_id,
            doc_id=doc_id,
            file_name=file_name,
            created_at=datetime.utcnow().isoformat() + "Z",
            pdf_path=str(uploaded_pdf_path),
            faiss_dir=str(faiss_dir),
            page_index_json=str(page_index_path),
            embedding_provider=self._models_factory.config.embed_model_provider,
            embedding_model=self._models_factory.config.embed_model,
        )

        (idx_dir / INDEX_META_FILENAME).write_text(
            json.dumps(meta.to_dict(), indent=2),
            encoding="utf-8",
        )

        return meta

    def get_index_meta(self, index_id: str) -> IndexMeta:
        meta_path = settings.indexes_dir() / index_id / INDEX_META_FILENAME
        if not meta_path.exists():
            raise FileNotFoundError(f"Index not found: {index_id}")
        return IndexMeta.from_path(meta_path)

    def get_pdf_path(self, index_id: str) -> Path:
        meta = self.get_index_meta(index_id)
        pdf_path = Path(meta.pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")
        return pdf_path

    def delete_uploaded_pdf(self, index_id: str) -> None:
        """
        Delete the uploaded PDF file associated with an index.
        """
        meta = self.get_index_meta(index_id)
        pdf_path = Path(meta.pdf_path)

        if pdf_path.exists():
            pdf_path.unlink(missing_ok=True)

    def delete_index(self, index_id: str) -> None:
        """
        Delete FAISS/index artifacts directory for a given index.
        Also removes any in-memory sessions attached to that index.
        """
        idx_dir = settings.indexes_dir() / index_id

        # Drop in-memory sessions bound to this index
        sessions_to_delete = [
            session_id
            for session_id, st in self._sessions.items()
            if st.index_id == index_id
        ]
        for session_id in sessions_to_delete:
            self._sessions.pop(session_id, None)

        if idx_dir.exists():
            shutil.rmtree(idx_dir, ignore_errors=True)

    # ----------------------------
    # Session management
    # ----------------------------

    def create_session(self, *, index_id: str) -> SessionState:
        meta = self.get_index_meta(index_id)

        chat_model: BaseChatModel = self._models_factory.chat_llm()
        embed_model = self._models_factory.embeddings()

        vector_store = FaissStore.from_index(embeddings=embed_model, dir_path=meta.faiss_dir)
        page_index = PageIndex.from_json_index(meta.page_index_json)

        repo = DocumentRepository(vector_store=vector_store, page_index=page_index)
        agent = ChatPDFAgent(chat_model=chat_model, document_repository=repo)

        session_id = f"sess_{uuid.uuid4().hex[:12]}"
        st = SessionState(session_id=session_id, index_id=index_id, created_at=datetime.utcnow(), agent=agent)

        self._sessions[session_id] = st
        return st

    def get_session(self, session_id: str) -> SessionState:
        st = self._sessions.get(session_id)
        if not st:
            raise KeyError(f"Session not found: {session_id}")
        return st

    def delete_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


    def cleanup_workspace(
        self,
        *,
        session_id: Optional[str] = None,
        index_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Best-effort cleanup for ephemeral/stateless demo usage.

        Order matters:
        1. delete session
        2. delete uploaded pdf
        3. delete index artifacts
        """
        result = {
            "session_deleted": False,
            "pdf_deleted": False,
            "index_deleted": False,
        }

        if session_id:
            self.delete_session(session_id)
            result["session_deleted"] = True

        if index_id:
            try:
                self.delete_uploaded_pdf(index_id)
                result["pdf_deleted"] = True
            except FileNotFoundError:
                # index meta may already be gone
                pass

            self.delete_index(index_id)
            result["index_deleted"] = True

        return result

    # ----------------------------
    # Chat / history / pages
    # ----------------------------

    def chat(self, session_id: str, user_message: str) -> AssistantResponse:
        st = self.get_session(session_id)
        raw_content = st.agent.invoke(user_message)
        return self._parse_llm_output(raw_content)

    async def stream_chat(self, session_id: str, user_message: str):
        st = self.get_session(session_id)
        full_content = ""

        async for event in st.agent.astream_events(user_message):
            if event["type"] == "tool":
                yield f"data: {json.dumps(event)}\n\n"
            elif event["type"] == "token":
                full_content += event["content"]

        result = self._parse_llm_output(full_content).model_dump()
        yield f"data: {json.dumps({'type': 'result', 'payload': result})}\n\n"
        yield "data: [DONE]\n\n"
    
    def list_session_messages(self, session_id: str) -> List[MessageView]:
        st = self.get_session(session_id)
        msgs: List[BaseMessage] = st.agent.state["messages"]
        out: List[Dict[str, str]] = []
        for m in msgs:
            c = str(getattr(m, "content", ""))
            role = self._msg_role(m)
            if role == 'assistant':
                assistant_response = self._parse_llm_output(c)
                out.append(assistant_response)
            else:
                out.append(MessageView(role=role, content=c))
        return out


    def _parse_llm_output(self, content: str) -> AssistantResponse :
        content = (content or "").strip()
        
        # tentative: JSON direct
        try:
            parsed_content = ChatPDFOutput.model_validate_json(content)
            return AssistantResponse(
                role="assistant",
                content=parsed_content.answer,
                sources=parsed_content.citations
            )
        except ValidationError:
            pass
        except json.JSONDecodeError:
            pass

        # fallback: extraction d'un bloc JSON {...}
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = content[start:end+1]
            try:
                parsed_content = ChatPDFOutput.model_validate_json(candidate)
                return AssistantResponse(
                    role="assistant",
                    content=parsed_content.answer,
                    sources=parsed_content.citations
                )
            except Exception:
                pass

        # fallback final: texte brut (l’UI affichera ça)
        return AssistantResponse(role="assistant", content=content)

    def _msg_role(self,m: BaseMessage) -> str:
        if isinstance(m, HumanMessage):
            return "user"
        if isinstance(m, AIMessage):
            return "assistant"
        if isinstance(m, ToolMessage):
            return "tool"
        if isinstance(m, SystemMessage):
            return "system"
        return "assistant"