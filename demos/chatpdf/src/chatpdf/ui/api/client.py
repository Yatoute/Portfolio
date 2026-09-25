from __future__ import annotations

import json
import requests
from typing import Iterator

from chatpdf.ui.api.errors import ApiError


class ChatPDFClient:
    def __init__(self, base_url: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"accept": "application/json"})

    def _request(self, method: str, path: str, **kwargs):
        url = f"{self.base_url}{path}"
        try:
            r = self.session.request(method, url, timeout=self.timeout, **kwargs)
        except requests.exceptions.Timeout as e:
            raise ApiError(408, f"Timeout calling {url}") from e
        except requests.exceptions.RequestException as e:
            raise ApiError(503, f"Network error calling {url}: {e}") from e

        if not r.ok:
            payload = r.text
            try:
                payload = r.json()
            except Exception:
                pass
            raise ApiError(r.status_code, payload)

        accept = None
        if "headers" in kwargs and kwargs["headers"]:
            accept = kwargs["headers"].get("accept")

        if accept == "application/pdf":
            return r.content

        return r.json()

    def create_document(
        self,
        file_bytes: bytes,
        filename: str,
        extract_images: bool = True,
        start_page: int | None = None,
        max_pages: int | None = None,
    ) -> dict:
        files = {"file": (filename, file_bytes, "application/pdf")}
        data = {
            "extract_images": str(extract_images).lower(),
            "start_page": "" if start_page is None else str(start_page),
            "max_pages": "" if max_pages is None else str(max_pages),
        }
        return self._request("POST", "/api/v1/documents", files=files, data=data)

    def get_index(self, index_id: str) -> dict:
        return self._request("GET", f"/api/v1/indexes/{index_id}")

    def get_pdf(self, index_id: str) -> bytes:
        return self._request(
            "GET",
            f"/api/v1/indexes/{index_id}/pdf",
            headers={"accept": "application/pdf"},
        )

    def create_session(self, index_id: str) -> dict:
        return self._request("POST", "/api/v1/sessions", json={"index_id": index_id})

    def get_messages(self, session_id: str) -> dict:
        return self._request("GET", f"/api/v1/sessions/{session_id}/messages")

    def send_message(self, session_id: str, message: str) -> dict:
        return self._request(
            "POST",
            f"/api/v1/sessions/{session_id}/messages",
            json={"message": message},
        )

    def stream_message(self, session_id: str, message: str) -> Iterator[dict]:
        url = f"{self.base_url}/api/v1/sessions/{session_id}/messages/stream"
        try:
            r = self.session.post(url, json={"message": message}, stream=True, timeout=self.timeout)
        except requests.exceptions.Timeout as e:
            raise ApiError(408, f"Timeout calling {url}") from e
        except requests.exceptions.RequestException as e:
            raise ApiError(503, f"Network error calling {url}: {e}") from e

        if not r.ok:
            raise ApiError(r.status_code, r.text)

        for line in r.iter_lines():
            if not line:
                continue
            line = line.decode("utf-8")
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            yield json.loads(data)

    def delete_session(self, session_id: str) -> dict:
        return self._request("DELETE", f"/api/v1/sessions/{session_id}")

    def delete_uploaded_pdf(self, index_id: str) -> dict:
        return self._request("DELETE", f"/api/v1/indexes/{index_id}/pdf")

    def delete_index(self, index_id: str) -> dict:
        return self._request("DELETE", f"/api/v1/indexes/{index_id}")

    def cleanup_workspace(self, session_id: str | None = None, index_id: str | None = None) -> dict:
        return self._request(
            "POST",
            "/api/v1/cleanup",
            json={"session_id": session_id, "index_id": index_id},
        )