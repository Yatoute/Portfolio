from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class APISettings:
    # Root folder where PDFs + indexes are stored
    storage_dir: Path = Path(os.getenv("CHATPDF_STORAGE_DIR", "./storage")).resolve()

    # Where uploaded PDFs go
    uploads_dirname: str = "uploads"

    # Where indexes go
    indexes_dirname: str = "indexes"

    # API
    api_prefix: str = os.getenv("CHATPDF_API_PREFIX", "/api/v1")

    # CORS — comma-separated list of allowed origins. Set to the real
    # portfolio domain in production; "*" is only meant for local dev.
    allowed_origins: str = os.getenv("CHATPDF_ALLOWED_ORIGINS", "*")

    # Rate limiting (per client IP), see slowapi limit-string syntax.
    rate_limit_upload: str = os.getenv("CHATPDF_RATE_LIMIT_UPLOAD", "3/day")
    rate_limit_chat: str = os.getenv("CHATPDF_RATE_LIMIT_CHAT", "20/hour")
    rate_limit_session: str = os.getenv("CHATPDF_RATE_LIMIT_SESSION", "10/hour")

    # Seed storage/ with a bundled example PDF + prebuilt index on first boot,
    # so a public demo is usable without requiring an upload.
    preload_example: bool = os.getenv("CHATPDF_PRELOAD_EXAMPLE", "true").lower() in ("1", "true", "yes")

    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    def uploads_dir(self) -> Path:
        p = self.storage_dir / self.uploads_dirname
        p.mkdir(parents=True, exist_ok=True)
        return p

    def indexes_dir(self) -> Path:
        p = self.storage_dir / self.indexes_dirname
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = APISettings()