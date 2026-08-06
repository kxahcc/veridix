from __future__ import annotations

from typing import Any

import httpx


class BurpConnector:
    """REST/Event-Bridge connector for a Burp instance or extension."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8081",
        api_token: str = "",
        event_bridge_url: str | None = None,
        timeout: float = 5.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_token = api_token
        self._event_bridge_url = event_bridge_url
        self._timeout = timeout

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def start_scan(self, target: str) -> str:
        payload = self._request(
            "POST",
            "/scans",
            body={"url": target},
        )
        return str(payload.get("scan_id") or payload.get("id") or "")

    def issues(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/issues")
        return list(payload.get("issues") or [])

    def export_observations(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for index, issue in enumerate(self.issues()):
            url = str(issue.get("url") or issue.get("path") or "")
            rows.append(
                {
                    "request_id": (
                        f"burp:{issue.get('id') or issue.get('issue_id') or index}"
                    ),
                    "web_session_id": "burp-connector",
                    "proxy_session_id": "burp-connector",
                    "method": str(issue.get("method") or "GET"),
                    "url": url,
                    "endpoint": url,
                    "status_code": int(issue.get("status_code") or 0),
                    "request_headers": dict(issue.get("request_headers") or {}),
                    "response_headers": dict(issue.get("response_headers") or {}),
                    "request_body": str(issue.get("request_body") or ""),
                    "response_body": str(
                        issue.get("detail")
                        or issue.get("evidence")
                        or issue.get("issue") or ""
                    ),
                    "content_type": "application/json",
                    "request_size": 0,
                    "response_size": 0,
                    "artifact_ref": (
                        f"artifact://burp/{issue.get('id') or issue.get('issue_id') or index}"
                    ),
                    "redacted": False,
                    "truncated": False,
                    "vuln_category": str(
                        issue.get("issue") or issue.get("title") or "burp_issue"
                    ),
                    "risk": str(issue.get("severity") or ""),
                }
            )
        return rows

    def send_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._event_bridge_url:
            raise RuntimeError("event bridge URL not configured")
        return self._request(
            "POST",
            self._event_bridge_url,
            body=payload,
            absolute=True,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        absolute: bool = False,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"
        url = path if absolute else f"{self._base_url}{path}"
        response = httpx.request(
            method,
            url,
            headers=headers,
            json=body,
            timeout=self._timeout,
            trust_env=False,
        )
        response.raise_for_status()
        return response.json()
