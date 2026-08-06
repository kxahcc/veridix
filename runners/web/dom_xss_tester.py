from __future__ import annotations

import json
import re
import secrets
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from runners.web.web_vuln_testers import _login, _set_security_low
from services.agent_runtime.kernel.contracts import (
    ExecutionRequest,
    ExecutionResult,
)


def _chromium_executable() -> str | None:
    base = Path(
        __import__("os").environ.get("LOCALAPPDATA", "")
    ) / "ms-playwright"
    candidates = sorted(
        base.glob("chromium-*/chrome-win/chrome.exe"),
        reverse=True,
    )
    return str(candidates[0]) if candidates else None


class DomXssTesterRunner:
    """Verifies DOM-based XSS by executing the page in a real browser."""

    def __init__(self, timeout: float = 25.0) -> None:
        self._timeout = timeout
        self.executions: list[ExecutionRequest] = []

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.executions.append(request)
        base = str(
            request.input.get("target")
            or request.input.get("url")
            or ""
        ).rstrip("/")
        if not base:
            return ExecutionResult(
                action_id=request.action_id,
                status="failed",
                exit_code=1,
                stderr="dom-xss tester requires target",
                side_effect_state="known",
            )
        username = str(request.input.get("username") or "admin")
        password = str(request.input.get("password") or "password")
        marker = str(
            request.input.get("marker")
            or f"N3TSEC{secrets.token_hex(4)}"
        )
        login_path = str(request.input.get("login_path") or "/login.php")
        security_path = str(
            request.input.get("security_path") or "/security.php"
        )
        dom_xss_path = str(
            request.input.get("dom_xss_path")
            or "/vulnerabilities/xss_d/"
        )
        login_fields = request.input.get("login_fields")
        set_security = bool(request.input.get("set_security", True))
        observation: dict[str, Any] = {
            "kind": "dom_xss",
            "endpoint": (
                dom_xss_path
                if dom_xss_path.startswith(("http://", "https://"))
                else f"{base}{dom_xss_path}"
            ),
            "method": "GET",
            "marker": marker,
        }
        try:
            with httpx.Client(
                base_url=base,
                timeout=self._timeout,
                verify=False,
                trust_env=False,
                follow_redirects=True,
            ) as client:
                if not _login(
                    client,
                    username=username,
                    password=password,
                    login_path=login_path,
                    login_fields=(
                        dict(login_fields) if login_fields else None
                    ),
                ):
                    observation["login"] = "failed"
                    return self._result(request, observation)
                _set_security_low(
                    client,
                    security_path=security_path,
                    enabled=set_security,
                )
                session_id = client.cookies.get("PHPSESSID")
                security = client.cookies.get("security")
            triggered, timeout_error = self._run_with_timeout(
                lambda: self._browser_check(
                    base,
                    marker,
                    session_id,
                    security,
                    dom_xss_path,
                ),
                self._timeout,
            )
            observation["triggered"] = triggered
            if timeout_error is not None:
                observation["error"] = str(timeout_error)
            if observation["triggered"]:
                observation["vuln_category"] = "XSS"
                observation["replay_proof"] = {
                    "marker": marker,
                    "path": f"{dom_xss_path}#default=...",
                }
        except Exception as error:
            observation["error"] = str(error)
        return self._result(request, observation)

    def _run_with_timeout(
        self,
        fn,
        timeout: float,
    ) -> tuple[Any, Exception | None]:
        box: dict[str, Any] = {}

        def target() -> None:
            try:
                box["value"] = fn()
            except Exception as error:
                box["error"] = error

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        thread.join(timeout=max(0.1, timeout))
        if thread.is_alive():
            return None, TimeoutError(
                f"browser check exceeded {timeout:.1f}s"
            )
        if "error" in box:
            return None, box["error"]
        return box.get("value"), None

    def _browser_check(
        self,
        base: str,
        marker: str,
        session_id: str | None,
        security: str | None,
        dom_xss_path: str,
    ) -> bool:
        from playwright.sync_api import sync_playwright

        host = urlparse(base).hostname or urlparse(base).netloc
        payload = (
            f"#default=<script>"
            f"document.body.setAttribute('data-veridix','{marker}')"
            f"</script>"
        )
        target_path = (
            dom_xss_path
            if dom_xss_path.startswith(("http://", "https://"))
            else f"{base}{dom_xss_path}"
        )
        for attempt in range(2):
            try:
                with sync_playwright() as playwright:
                    launch_kwargs: dict[str, Any] = {"headless": True}
                    executable = _chromium_executable()
                    if executable:
                        launch_kwargs["executable_path"] = executable
                    browser = playwright.chromium.launch(**launch_kwargs)
                    try:
                        context = browser.new_context()
                        if session_id:
                            context.add_cookies(
                                [
                                    {
                                        "name": "PHPSESSID",
                                        "value": session_id,
                                        "domain": host,
                                        "path": "/",
                                    },
                                    {
                                        "name": "security",
                                        "value": security or "low",
                                        "domain": host,
                                        "path": "/",
                                    },
                                ]
                            )
                        page = context.new_page()
                        page.set_default_timeout(8000)
                        page.goto(
                            f"{target_path}{payload}",
                            wait_until="domcontentloaded",
                            timeout=8000,
                        )
                        page.wait_for_timeout(1200)
                        value = page.evaluate(
                            "document.body.getAttribute('data-veridix') || ''"
                        )
                        if marker in str(value):
                            return True
                    finally:
                        browser.close()
            except Exception:
                if attempt == 2:
                    raise
        return False

    def _result(
        self,
        request: ExecutionRequest,
        observation: dict[str, Any],
    ) -> ExecutionResult:
        return ExecutionResult(
            action_id=request.action_id,
            status="completed",
            exit_code=0,
            stdout=json.dumps(
                {"observation_count": 1},
                ensure_ascii=True,
            ),
            observations=(observation,),
            artifact_refs=(
                f"artifact://dom-xss/{request.action_id}",
            ),
            side_effect_state="known",
        )
