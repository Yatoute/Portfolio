from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from chatpdf.agent import Citation

class DocumentCreateResponse(BaseModel):
    doc_id: str
    index_id: str
    file_name: str
    total_pages: Optional[int] = None
    artifacts: Dict[str, str]


class IndexInfoResponse(BaseModel):
    index_id: str
    doc_id: str
    file_name: str
    created_at: datetime
    paths: Dict[str, str]
    embedding_provider: Optional[str] = None
    embedding_model: Optional[str] = None


class SessionCreateRequest(BaseModel):
    index_id: str
    # chat_provider: Optional[Literal["ollama", "openai"]] = None
    # embeddings_provider: Optional[Literal["ollama", "openai"]] = None
    # # Retriever defaults (the agent/tools may override at runtime)
    # k: int = Field(5, ge=1, le=20)
    # search_type: Literal["mmr", "similarity"] = "mmr"


class SessionCreateResponse(BaseModel):
    session_id: str
    index_id: str
    created_at: datetime


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)

class SessionInfoResponse(BaseModel):
    session_id: str
    index_id: str
    created_at: datetime
    message_count: int


class MessageView(BaseModel):
    role: Literal["user", "assistant", "tool", "system"]
    content: str

class AssistantResponse(BaseModel):
    role: Literal["assistant"]="assistant"
    content: str
    sources: List[Citation] = []

class MessagesResponse(BaseModel):
    session_id: str
    messages: List[MessageView | AssistantResponse]

class PageResponse(BaseModel):
    doc_id: str
    file_name: str
    page_number: int
    text: str

class CleanupRequest(BaseModel):
    session_id: str | None = None
    index_id: str | None = None

class HeartbeatRequest(BaseModel):
    index_id: str