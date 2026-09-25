from __future__ import annotations

import json
import logging
import os
import subprocess
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from jobs import manager
from pipeline.translation import SUPPORTED_LANGUAGES, translate as translate_text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("video-mining")

APP_STORAGE_DIR = Path(os.environ.get("VIDEOMINING_STORAGE_DIR", "./storage")).resolve()
APP_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_BYTES = int(os.environ.get("VIDEOMINING_MAX_UPLOAD_MB", "25")) * 1024 * 1024
MAX_DURATION_SECONDS = int(os.environ.get("VIDEOMINING_MAX_DURATION_S", "180"))

RATE_LIMIT_UPLOAD = os.environ.get("VIDEOMINING_RATE_LIMIT_UPLOAD", "5/hour")
RATE_LIMIT_TRANSLATE = os.environ.get("VIDEOMINING_RATE_LIMIT_TRANSLATE", "30/hour")

ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("VIDEOMINING_ALLOWED_ORIGINS", "*").split(",") if o.strip()]

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Video Mining Demo")
app.state.limiter = limiter

templates = Jinja2Templates(directory="templates")


@app.exception_handler(RateLimitExceeded)
def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Too many requests. Please try again later."})


def _probe_duration_seconds(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
        capture_output=True,
        text=True,
        timeout=30,
    )
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
            "max_duration_s": MAX_DURATION_SECONDS,
            "languages": SUPPORTED_LANGUAGES,
        },
    )


@app.post("/api/upload")
@limiter.limit(RATE_LIMIT_UPLOAD)
async def upload_video(request: Request, file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith("video/"):
        raise HTTPException(status_code=400, detail="Please upload a video file.")

    work_dir = APP_STORAGE_DIR / uuid.uuid4().hex[:12]
    work_dir.mkdir(parents=True, exist_ok=True)
    video_path = work_dir / f"source{Path(file.filename or 'video.mp4').suffix or '.mp4'}"

    size = 0
    with video_path.open("wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                f.close()
                video_path.unlink(missing_ok=True)
                work_dir.rmdir()
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large. Limit is {MAX_UPLOAD_BYTES // (1024*1024)}MB for this demo.",
                )
            f.write(chunk)

    try:
        duration = _probe_duration_seconds(str(video_path))
    except Exception:
        video_path.unlink(missing_ok=True)
        work_dir.rmdir()
        raise HTTPException(status_code=400, detail="Could not read this video file.")

    if duration > MAX_DURATION_SECONDS:
        video_path.unlink(missing_ok=True)
        work_dir.rmdir()
        raise HTTPException(
            status_code=413,
            detail=f"Video too long ({duration:.0f}s). Limit is {MAX_DURATION_SECONDS}s for this demo.",
        )

    try:
        job = manager.submit(str(video_path), str(work_dir))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return {"job_id": job.id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return {
        "id": job.id,
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "error": job.error,
    }


@app.get("/result/{job_id}", response_class=HTMLResponse)
def result_page(request: Request, job_id: str):
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return templates.TemplateResponse(request, "result.html", {"job_id": job_id})


@app.get("/api/jobs/{job_id}/result")
def job_result(job_id: str):
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status != "done":
        raise HTTPException(status_code=409, detail=f"Job is {job.status}, not ready yet.")
    return job.result


@app.get("/api/jobs/{job_id}/video")
def job_video(job_id: str):
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return FileResponse(job.video_path)


@app.get("/api/jobs/{job_id}/keyframes/{filename}")
def job_keyframe(job_id: str, filename: str):
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    # Strip any directory components before joining — filename comes from the
    # URL path, os.path.basename() prevents path traversal (e.g. "../../etc/passwd").
    safe_name = os.path.basename(filename)
    path = Path(job.work_dir) / "keyframes" / safe_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Keyframe not found.")
    return FileResponse(path)


@app.post("/api/translate")
@limiter.limit(RATE_LIMIT_TRANSLATE)
async def translate_endpoint(request: Request):
    body = await request.json()
    text = (body.get("text") or "").strip()
    # from_code is optional — omitted or "auto" triggers fastText detection,
    # same as production.
    from_code = body.get("from_code")
    if from_code in (None, "", "auto"):
        from_code = None
    to_code = body.get("to_code")

    if not text:
        raise HTTPException(status_code=400, detail="Missing text.")
    if len(text) > 2000:
        raise HTTPException(status_code=400, detail="Text too long for this demo (2000 chars max).")

    try:
        translated, detected_source = translate_text(text, to_code, from_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        logger.exception("Translation failed")
        raise HTTPException(status_code=500, detail="Translation failed.")

    return {"translated": translated, "detected_source": detected_source}


app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
