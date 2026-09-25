from __future__ import annotations

class ApiError(RuntimeError):
    def __init__(self, status_code: int, body: str):
        super().__init__(f"API error {status_code}: {body}")
        self.status_code = status_code
        self.body = body