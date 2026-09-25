from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from chatpdf.api.config import settings
from chatpdf.api.limiter import limiter
from chatpdf.api.routers import api_router
from chatpdf.api.preload import maybe_preload_example

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="ChatPDF API", version="0.1.0")

app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please try again later."},
    )


# CORS — restrict to the real portfolio domain in production via
# CHATPDF_ALLOWED_ORIGINS (comma-separated). Defaults to "*" for local dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, tags=["ChatPDF API"])


@app.on_event("startup")
def _startup() -> None:
    if settings.preload_example:
        maybe_preload_example()


def run():
    import uvicorn
    uvicorn.run(
        "chatpdf.api.main:app",
        host="127.0.0.1",
        port=8000
    )
