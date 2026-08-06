from __future__ import annotations

import json
import time
from typing import Any

import httpx


class ZapConnector:
    """Connector that turns a ZAP instance into Veridix web observations."""

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
        return self._json("GET", "/JSON/core/view/version/")

    def spider(self, target: str) -> dict[str, Any]:
        response = self._json(
            "GET",
            "/JSON/spider/action/scan/",
            params={"url": target},
        )
        scan_id = str(response.get("scan", ""))
        return self._wait(
            "/JSON/spider/view/status/",
            {"scanId": scan_id},
            response,
        )

    def active_scan(self, target: str) -> dict[str, Any]:
        response = self._json(
            "GET",
            "/JSON/ascan/action/scan/",
            params={"url": target},
        )
        scan_id = str(response.get("scan", ""))
        return self._wait(
            "/JSON/ascan/view/status/",
            {"scanId": scan_id},
            response,
        )

    def alerts(self) -> list[dict[str, Any]]:
        payload = self._json("GET", "/JSON/alert/view/alerts/")
        return list(payload.get("alerts") or [])

    def export_observations(self) -> list[dict[str, Any]]:
        return self.observations_from_alerts(self.alerts())

    @staticmethod
    def observations_from_alerts(
        alerts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for index, alert in enumerate(alerts):
            url = str(alert.get("url") or "")
            rows.append(
                {
                    "request_id": f"zap:{alert.get('id', index)}",
                    "web_session_id": "zap-connector",
                    "proxy_session_id": "zap-connector",
                    "method": str(alert.get("method") or "GET"),
                    "url": url,
                    "endpoint": f"{alert.get('method') or 'GET'} {url}",
                    "status_code": 0,
                    "request_headers": {},
                    "response_headers": {},
                    "request_body": "",
                    "response_body": str(
                        alert.get("evidence") or alert.get("description") or ""
                    ),
                    "content_type": "application/json",
                    "request_size": 0,
                    "response_size": 0,
                    "artifact_ref": f"artifact://zap/{alert.get('id', index)}",
                    "redacted": False,
                    "truncated": False,
                    "vuln_category": str(
                        alert.get("alert") or "zap_alert"
                    ),
                    "risk": str(alert.get("risk") or ""),
                }
            )
        return rows

    def _wait(
        self,
        path: str,
        params: dict[str, str],
        started: dict[str, Any],
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self._json(
                "GET",
                path,
                params=params,
                timeout=max(15.0, self._timeout),
            )
            if str(status.get("status")) == "100":
                return status
            time.sleep(1.0)
        return {**started, "status": "timeout"}

    def _json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        query = dict(params or {})
        if self._api_key:
            query["apikey"] = self._api_key
        response = httpx.request(
            method,
            f"{self._base_url}{path}",
            params=query,
            timeout=timeout or self._timeout,
            trust_env=False,
        )
        response.raise_for_status()
        return response.json()


class ZapDockerConnector(ZapConnector):
    """Starts ZAP as a Docker container and connects to its REST API."""

    def __init__(
        self,
        *,
        image: str = "veridix-zap:full",
        port: int = 8080,
        api_key: str = "",
        timeout: float = 5.0,
    ) -> None:
        super().__init__(
            base_url=f"http://127.0.0.1:{port}",
            api_key=api_key,
            timeout=timeout,
        )
        self._image = image
        self._port = port
        self._container = None

    def start(self) -> "ZapDockerConnector":
        import docker

        client = docker.from_env()
        self._container = client.containers.run(
            self._image,
            detach=True,
            ports={f"{self._port}/tcp": self._port},
            command=[
                "/zap/zap.sh",
                "-daemon",
                "-host",
                "0.0.0.0",
                "-port",
                str(self._port),
                "-config",
                "api.disablekey=true",
            ],
        )
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                if self.health().get("version"):
                    return self
            except Exception:
                pass
            time.sleep(2.0)
        raise RuntimeError("ZAP container did not become ready")

    def stop(self) -> None:
        if self._container is not None:
            self._container.remove(force=True)
            self._container = None
