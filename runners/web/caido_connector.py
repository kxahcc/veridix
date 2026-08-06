from __future__ import annotations

import time
from typing import Any

import httpx


class CaidoConnector:
    """Connector that turns a Caido instance into Veridix web observations."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8080",
        api_key: str = "",
        timeout: float = 5.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    def health(self) -> dict[str, Any]:
        return self._graphql("query { __typename }")

    def create_scan(
        self,
        target: str,
        *,
        kind: str = "ACTIVE",
    ) -> str:
        data = self._graphql(
            """
            mutation CreateScan($kind: ScanKind!, $target: String!) {
              createScan(input: {kind: $kind, target: {id: $target}}) {
                scan { id }
              }
            }
            """,
            {"kind": kind, "target": target},
        )
        return str(data["createScan"]["scan"]["id"])

    def scan_status(
        self,
        scan_id: str,
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = self._graphql(
                """
                query ScanStatus($id: ID!) {
                  scan(id: $id) { id status }
                }
                """,
                {"id": scan_id},
            )
            scan = data.get("scan") or {}
            if scan.get("status") in (
                "COMPLETED",
                "FAILED",
                "CANCELED",
            ):
                return scan
            time.sleep(1.0)
        return {"id": scan_id, "status": "timeout"}

    def issues(self) -> list[dict[str, Any]]:
        data = self._graphql(
            """
            query Issues {
              issues { nodes { id title severity path detail } }
            }
            """
        )
        return list((data.get("issues") or {}).get("nodes") or [])

    def export_observations(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for index, issue in enumerate(self.issues()):
            path = str(issue.get("path") or "")
            rows.append(
                {
                    "request_id": f"caido:{issue.get('id', index)}",
                    "web_session_id": "caido-connector",
                    "proxy_session_id": "caido-connector",
                    "method": "GET",
                    "url": path,
                    "endpoint": path,
                    "status_code": 0,
                    "request_headers": {},
                    "response_headers": {},
                    "request_body": "",
                    "response_body": str(
                        issue.get("detail") or issue.get("title") or ""
                    ),
                    "content_type": "application/json",
                    "request_size": 0,
                    "response_size": 0,
                    "artifact_ref": (
                        f"artifact://caido/{issue.get('id', index)}"
                    ),
                    "redacted": False,
                    "truncated": False,
                    "vuln_category": str(issue.get("title") or "caido_issue"),
                    "risk": str(issue.get("severity") or ""),
                }
            )
        return rows

    def _graphql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        response = httpx.post(
            f"{self._base_url}/graphql",
            headers=headers,
            json={"query": query, "variables": variables or {}},
            timeout=self._timeout,
            trust_env=False,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(str(payload["errors"]))
        return payload.get("data") or {}
