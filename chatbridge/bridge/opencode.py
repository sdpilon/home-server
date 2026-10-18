"""A very small client for the OpenCode HTTP API.

Only the handful of endpoints the bridge needs are implemented. The API is
served by the OpenCode background service and authenticated with HTTP Basic,
using the fixed username ``opencode`` and the password from
``~/.config/opencode/service.json``.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Any, Optional


class OpenCodeError(RuntimeError):
    """Raised when the OpenCode API returns a non-2xx response."""

    def __init__(self, status: int, payload: Any):
        self.status = status
        self.payload = payload
        super().__init__(f"HTTP {status}: {payload}")


class OpenCodeClient:
    def __init__(self, url: str, password: str, timeout: float = 60.0) -> None:
        self.url = url.rstrip("/")
        token = base64.b64encode(f"opencode:{password}".encode()).decode()
        self._auth = f"Basic {token}"
        self.timeout = timeout

    # ------------------------------------------------------------------ core

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("Authorization", self._auth)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload: Any = json.loads(raw)
            except Exception:
                payload = raw.decode("utf-8", "replace")
            raise OpenCodeError(exc.code, payload) from None
        except urllib.error.URLError as exc:
            raise OpenCodeError(0, f"cannot reach {self.url}: {exc.reason}") from None
        if not raw:
            return None
        return json.loads(raw)

    @staticmethod
    def _data(response: Any) -> Any:
        """Unwrap the ``{"data": ...}`` envelope used by most endpoints."""
        if isinstance(response, dict) and "data" in response:
            return response["data"]
        return response

    # --------------------------------------------------------------- helpers

    def info(self) -> dict:
        """Server info. Also used as a cheap health probe."""
        return self._request("GET", "/api/info", timeout=10)

    def create_session(
        self,
        *,
        title: str,
        directory: str,
        agent: Optional[str] = None,
        model: Optional[dict] = None,
    ) -> dict:
        body: dict[str, Any] = {"title": title, "location": {"directory": directory}}
        if agent:
            body["agent"] = agent
        if model:
            body["model"] = model
        return self._data(self._request("POST", "/api/session", body))

    def get_session(self, session_id: str) -> dict:
        return self._data(self._request("GET", f"/api/session/{session_id}"))

    def session_exists(self, session_id: str) -> bool:
        try:
            self.get_session(session_id)
            return True
        except OpenCodeError as exc:
            if exc.status == 404:
                return False
            raise

    def prompt(self, session_id: str, text: str) -> None:
        self._request("POST", f"/api/session/{session_id}/prompt", {"text": text})

    def messages(self, session_id: str) -> list:
        """Session messages, newest first (as returned by the server)."""
        data = self._data(self._request("GET", f"/api/session/{session_id}/message"))
        return data or []

    def pending_permissions(self, session_id: str) -> list:
        try:
            data = self._data(self._request("GET", f"/api/session/{session_id}/permission"))
        except OpenCodeError:
            return []
        return data or []

    def reply_permission(self, session_id: str, request_id: str, decision: str) -> None:
        """decision is one of ``once``, ``always`` or ``reject``."""
        self._request(
            "POST",
            f"/api/session/{session_id}/permission/{request_id}/reply",
            {"decision": decision},
        )

    def wait(self, session_id: str, timeout: Optional[float] = None) -> None:
        """Block until the session has no execution in flight.

        This is the reliable "run finished" signal: a run that ends because the
        user declined a command produces no ``idle`` message, but the session
        does become idle, so this returns for that case too.
        """
        self._request(
            "POST",
            f"/api/experimental/session/{session_id}/wait",
            {},
            timeout=timeout or 3600,
        )

    def interrupt(self, session_id: str) -> None:
        self._request("POST", f"/api/session/{session_id}/interrupt", {})

    def delete_session(self, session_id: str) -> None:
        self._request("DELETE", f"/api/session/{session_id}")
