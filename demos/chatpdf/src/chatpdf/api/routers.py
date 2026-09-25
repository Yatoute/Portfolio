from __future__ import annotations

import os
import logging
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Annotated

from pydantic import conint
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
import json

from chatpdf.utils import clean_filename
from chatpdf.api.config import settings
from chatpdf.api.limiter import limiter
from chatpdf.api.preload import get_default_index_id
from chatpdf.api.schemas import (
    HeartbeatRequest,
    ChatRequest,
    DocumentCreateResponse,
    IndexInfoResponse,
    MessagesResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionInfoResponse,
    AssistantResponse,
    CleanupRequest
)

from chatpdf.api.services import ChatPDFService

api_router = APIRouter()
    
# Initialize ChatPDFService
chatpdf_service = ChatPDFService()

API_PREFIX = settings.api_prefix


@api_router.get(f"{API_PREFIX}/health")
def health():
    return {"status": "Hello, I am alive", "time": datetime.utcnow().isoformat() + "Z"}

@api_router.post(f"{API_PREFIX}/heartbeat")
def heartbeat(req: HeartbeatRequest):
    chatpdf_service.record_heartbeat(req.index_id)
    return {"status": "ok"}

StartPage = Optional[conint(ge=1)]
MaxPages  = Optional[conint(ge=1)]

@api_router.get(f"{API_PREFIX}/default-index")
def default_index():
    index_id = get_default_index_id()
    if not index_id:
        raise HTTPException(status_code=404, detail="No default index configured.")
    return {"index_id": index_id}


@api_router.post(f"{API_PREFIX}/documents", response_model=DocumentCreateResponse)
@limiter.limit(settings.rate_limit_upload)
async def upload_and_index_document(
    request: Request,
    file: UploadFile = File(...),
    extract_images: bool = Form(True),
    start_page: Annotated[StartPage, Form()] = None,
    max_pages: Annotated[MaxPages, Form()] = None,
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    uploads_dir = settings.uploads_dir()
  
    name, ext = os.path.splitext(file.filename)
    cleaned_name = clean_filename(name)
    safe_name = f"{cleaned_name}-{uuid.uuid4().hex[:12]}{ext}"

    dest = uploads_dir / safe_name

    content = await file.read()
    dest.write_bytes(content)

    try:
        meta = chatpdf_service.create_index_from_upload(
            uploaded_pdf_path=dest,
            file_name=safe_name,
            extract_images=extract_images,
            start_page=start_page,
            max_pages=max_pages
        )
        # Record heartbeat
        chatpdf_service.record_heartbeat(meta.index_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Indexing failed: {e}")
    
    # Total pages
    total_pages = None
    try:
        page_index_data = json.loads(Path(meta.page_index_json).read_text(encoding="utf-8"))
        if isinstance(page_index_data, dict):
            total_pages = len(page_index_data.get(meta.file_name, {}))
    except Exception:
        pass

    return DocumentCreateResponse(
        doc_id=meta.doc_id,
        index_id=meta.index_id,
        file_name=meta.file_name,
        total_pages=total_pages,
        artifacts={
            "faiss_dir": meta.faiss_dir,
            "page_index_json": meta.page_index_json,
        },
    )


@api_router.get(f"{API_PREFIX}/indexes/{{index_id}}", response_model=IndexInfoResponse)
def get_index(index_id: str):
    try:
        meta = chatpdf_service.get_index_meta(index_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Index not found.")

    return IndexInfoResponse(
        index_id=meta.index_id,
        doc_id=meta.doc_id,
        file_name=meta.file_name,
        created_at=datetime.fromisoformat(meta.created_at.replace("Z", "+00:00")),
        paths={
            "pdf_path": meta.pdf_path,
            "faiss_dir": meta.faiss_dir,
            "page_index_json": meta.page_index_json,
        },
        embedding_provider=meta.embedding_provider,
        embedding_model=meta.embedding_model,
    )


@api_router.post(f"{API_PREFIX}/sessions", response_model=SessionCreateResponse)
@limiter.limit(settings.rate_limit_session)
def create_session(request: Request, req: SessionCreateRequest):

    try:
        st = chatpdf_service.create_session(index_id=req.index_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Index not found.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Session creation failed: {e}")

    return SessionCreateResponse(
        session_id=st.session_id,
        index_id=st.index_id,
        created_at=st.created_at
    )


@api_router.get(f"{API_PREFIX}/sessions/{{session_id}}", response_model=SessionInfoResponse)
def get_session_info(session_id: str):
    try:
        st = chatpdf_service.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found.")

    msgs = st.agent.state["messages"]

    return SessionInfoResponse(
        session_id=st.session_id,
        index_id=st.index_id,
        created_at=st.created_at,
        message_count=len(msgs)
    )


@api_router.get(f"{API_PREFIX}/sessions/{{session_id}}/messages", response_model=MessagesResponse)
def get_session_messages(session_id: str):
    try:
        lst_session_messages = chatpdf_service.list_session_messages(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found.")

    return MessagesResponse(
        session_id=session_id,
        messages=lst_session_messages,
    )


@api_router.delete(f"{API_PREFIX}/sessions/{{session_id}}")
def delete_session(session_id: str):
    chatpdf_service.delete_session(session_id)
    return {"status": "deleted", "session_id": session_id}


@api_router.post(f"{API_PREFIX}/sessions/{{session_id}}/messages", response_model=AssistantResponse)
@limiter.limit(settings.rate_limit_chat)
def post_message(request: Request, session_id: str, req: ChatRequest):
    try:
        assistant_response = chatpdf_service.chat(session_id, req.message)
    
    except KeyError as e:
        logging.error(f"Chat failed: {e}")
        raise HTTPException(status_code=404, detail="Session not found.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chat failed: {e}")

    return assistant_response

@api_router.post(f"{API_PREFIX}/sessions/{{session_id}}/messages/stream")
@limiter.limit(settings.rate_limit_chat)
async def stream_message(request: Request, session_id: str, req: ChatRequest):
    try:
        chatpdf_service.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found.")

    return StreamingResponse(
        chatpdf_service.stream_chat(session_id, req.message),
        media_type="text/event-stream"
    )
    
@api_router.get(f"{API_PREFIX}/indexes/{{index_id}}/pdf")
def get_pdf(index_id: str):
    try:
        pdf_path = chatpdf_service.get_pdf_path(index_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="PDF not found.")


    headers = {
        "Content-Disposition": f'inline; filename="{pdf_path.name}"'
    }

    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        headers=headers,
    )

@api_router.delete(f"{API_PREFIX}/indexes/{{index_id}}")
def delete_index(index_id: str):
    try:
        chatpdf_service.cancel_heartbeat(index_id)
        chatpdf_service.delete_index(index_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Index deletion failed: {e}")

    return {"status": "deleted", "index_id": index_id}


@api_router.delete(f"{API_PREFIX}/indexes/{{index_id}}/pdf")
def delete_uploaded_pdf(index_id: str):
    try:
        chatpdf_service.delete_uploaded_pdf(index_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Index not found.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF deletion failed: {e}")

    return {"status": "deleted", "index_id": index_id}


@api_router.post(f"{API_PREFIX}/cleanup")
def cleanup_workspace(req: CleanupRequest):
    if req.index_id:
        chatpdf_service.cancel_heartbeat(req.index_id)
    try:
        result = chatpdf_service.cleanup_workspace(
            session_id=req.session_id,
            index_id=req.index_id,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cleanup failed: {e}")

    return {
        "status": "cleaned", 
        "session_id": req.session_id, 
        "index_id": req.index_id, 
        **result
    }