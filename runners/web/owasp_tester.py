from __future__ import annotations

import json
import re
import secrets
from typing import Any

import httpx

from runners.web.web_vuln_testers import _login, _set_security_low, _token
from services.agent_runtime.kernel.contracts import (
    ExecutionRequest,
    ExecutionResult,
)


class OwaspTesterRunner:
    """Runs one OWASP-style web check per execution."""

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
        check = str(request.input.get("check") or "security_headers")
        username = str(request.input.get("username") or "admin")
        password = str(request.input.get("password") or "password")
        paths = {
            "login": str(
                request.input.get("login_path") or "/login.php"
            ),
            "security": str(
                request.input.get("security_path") or "/security.php"
            ),
            "exec": str(
                request.input.get("exec_path")
                or "/vulnerabilities/exec/"
            ),
            "backup": str(
                request.input.get("backup_path")
                or "/config/config.inc.php.bak"
            ),
            "xss_r": str(
                request.input.get("xss_r_path")
                or "/vulnerabilities/xss_r/"
            ),
            "xss_s": str(
                request.input.get("xss_s_path")
                or "/vulnerabilities/xss_s/"
            ),
            "csrf": str(
                request.input.get("csrf_path")
                or "/vulnerabilities/csrf/"
            ),
            "weak_id": str(
                request.input.get("weak_id_path")
                or "/vulnerabilities/weak_id/"
            ),
            "setup": str(
                request.input.get("setup_path") or "/setup.php"
            ),
            "phpinfo": str(
                request.input.get("phpinfo_path") or "/phpinfo.php"
            ),
            "phpids": str(
                request.input.get("phpids_path")
                or "/external/phpids/0.6/lib/IDS/tmp/phpids_log.txt"
            ),
            "config_dir": str(
                request.input.get("config_dir") or "/config/"
            ),
            "uploads_dir": str(
                request.input.get("uploads_dir")
                or "/hackable/uploads/"
            ),
            "login_fields": request.input.get("login_fields"),
            "set_security": bool(
                request.input.get("set_security", True)
            ),
        }
        if not base:
            return ExecutionResult(
                action_id=request.action_id,
                status="failed",
                exit_code=1,
                stderr="owasp tester requires target",
                side_effect_state="known",
            )
        try:
            if check == "command_injection":
                observations = self._command_injection(
                    base,
                    username,
                    password,
                    paths,
                )
            elif check == "backup_file":
                observations = self._backup_file(base, paths)
            elif check == "xss_reflected":
                observations = self._xss_reflected(
                    base,
                    username,
                    password,
                    paths,
                )
            elif check == "xss_stored":
                observations = self._xss_stored(
                    base,
                    username,
                    password,
                    paths,
                )
            elif check == "rate_limit":
                observations = self._rate_limit(base, paths)
            elif check == "csrf":
                observations = self._csrf(
                    base,
                    username,
                    password,
                    paths,
                )
            elif check == "weak_session":
                observations = self._weak_session(
                    base,
                    username,
                    password,
                    paths,
                )
            elif check == "info_disclosure":
                observations = self._info_disclosure(
                    base,
                    username,
                    password,
                    paths,
                )
            else:
                observations = self._security_headers(base, paths)
        except httpx.HTTPError as error:
            observations = (
                {
                    "kind": "owasp_error",
                    "check": check,
                    "error": str(error),
                },
            )
        return ExecutionResult(
            action_id=request.action_id,
            status="completed",
            exit_code=0,
            stdout=json.dumps(
                {"observation_count": len(observations)},
                ensure_ascii=True,
            ),
            observations=observations,
            artifact_refs=(
                f"artifact://owasp/{check}/{request.action_id}",
            ),
            side_effect_state="known",
        )

    def _command_injection(
        self,
        base: str,
        username: str,
        password: str,
        paths: dict[str, str],
    ) -> tuple[dict[str, Any], ...]:
        marker = f"N3TSEC{secrets.token_hex(4)}"
        observation: dict[str, Any] = {
            "kind": "command_injection",
            "endpoint": f"{base}{paths['exec']}",
            "method": "POST",
            "marker": marker,
        }
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
                login_path=paths["login"],
                login_fields=(
                    dict(paths["login_fields"])
                    if paths.get("login_fields")
                    else None
                ),
            ):
                observation["login"] = "failed"
                return (observation,)
            _set_security_low(
                client,
                security_path=paths["security"],
                enabled=paths.get("set_security", True),
            )
            page = client.get(paths["exec"])
            data = {
                "ip": f"127.0.0.1; echo {marker}",
                "Submit": "Submit",
            }
            token = _token(page.text)
            if token:
                data["user_token"] = token
            response = client.post(paths["exec"], data=data)
            observation["status"] = response.status_code
            observation["marker_present"] = marker in response.text
            if observation["marker_present"]:
                observation["vuln_category"] = "CommandInjection"
                observation["replay_proof"] = {
                    "marker": marker,
                    "status": observation["status"],
                }
        return (observation,)

    def _backup_file(
        self,
        base: str,
        paths: dict[str, str],
    ) -> tuple[dict[str, Any], ...]:
        path = paths["backup"]
        observation: dict[str, Any] = {
            "kind": "backup_file_exposure",
            "endpoint": f"{base}{path}",
            "method": "GET",
        }
        with httpx.Client(
            base_url=base,
            timeout=self._timeout,
            verify=False,
            trust_env=False,
            follow_redirects=True,
        ) as client:
            response = client.get(path)
            observation["status"] = response.status_code
            observation["contains_credentials"] = (
                response.status_code == 200
                and (
                    "DB_PASSWORD" in response.text
                    or "db_password" in response.text
                    or "$DB" in response.text
                )
            )
            if observation["contains_credentials"]:
                observation["vuln_category"] = "InformationDisclosure"
                observation["replay_proof"] = {
                    "path": path,
                    "status": response.status_code,
                    "file": "config.inc.php.bak",
                }
        return (observation,)

    def _xss_reflected(
        self,
        base: str,
        username: str,
        password: str,
        paths: dict[str, str],
    ) -> tuple[dict[str, Any], ...]:
        marker = f"N3TSEC<script>alert(1)</script>"
        observation: dict[str, Any] = {
            "kind": "reflected_xss",
            "endpoint": f"{base}{paths['xss_r']}",
            "method": "GET",
            "marker": marker,
        }
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
                login_path=paths["login"],
                login_fields=(
                    dict(paths["login_fields"])
                    if paths.get("login_fields")
                    else None
                ),
            ):
                observation["login"] = "failed"
                return (observation,)
            _set_security_low(
                client,
                security_path=paths["security"],
                enabled=paths.get("set_security", True),
            )
            response = client.get(
                paths["xss_r"],
                params={"name": marker},
            )
            observation["status"] = response.status_code
            observation["reflected"] = marker in response.text
            if observation["reflected"]:
                observation["vuln_category"] = "XSS"
                observation["replay_proof"] = {
                    "parameter": "name",
                    "marker": marker,
                    "status": response.status_code,
                }
        return (observation,)

    def _xss_stored(
        self,
        base: str,
        username: str,
        password: str,
        paths: dict[str, str],
    ) -> tuple[dict[str, Any], ...]:
        marker = f"N3TSEC<script>alert(2)</script>"
        observation: dict[str, Any] = {
            "kind": "stored_xss",
            "endpoint": f"{base}{paths['xss_s']}",
            "method": "POST",
            "marker": marker,
        }
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
                login_path=paths["login"],
                login_fields=(
                    dict(paths["login_fields"])
                    if paths.get("login_fields")
                    else None
                ),
            ):
                observation["login"] = "failed"
                return (observation,)
            _set_security_low(
                client,
                security_path=paths["security"],
                enabled=paths.get("set_security", True),
            )
            page = client.get(paths["xss_s"])
            data = {
                "txtName": marker,
                "mtxMessage": "stored xss check",
                "btnSign": "Sign Guestbook",
            }
            token = _token(page.text)
            if token:
                data["user_token"] = token
            client.post(paths["xss_s"], data=data)
            response = client.get(paths["xss_s"])
            observation["status"] = response.status_code
            observation["stored"] = marker in response.text
            if observation["stored"]:
                observation["vuln_category"] = "XSS"
                observation["replay_proof"] = {
                    "parameter": "txtName",
                    "marker": marker,
                    "status": response.status_code,
                }
        return (observation,)

    def _rate_limit(
        self,
        base: str,
        paths: dict[str, str],
    ) -> tuple[dict[str, Any], ...]:
        observation: dict[str, Any] = {
            "kind": "rate_limit",
            "endpoint": f"{base}{paths['login']}",
            "method": "POST",
        }
        statuses: list[int] = []
        with httpx.Client(
            base_url=base,
            timeout=self._timeout,
            verify=False,
            trust_env=False,
            follow_redirects=True,
        ) as client:
            for _ in range(6):
                page = client.get(paths["login"])
                data = {
                    "username": "admin",
                    "password": f"wrong{secrets.token_hex(3)}",
                    "Login": "Login",
                }
                token = _token(page.text)
                if token:
                    data["user_token"] = token
                response = client.post(paths["login"], data=data)
                statuses.append(response.status_code)
        observation["statuses"] = statuses
        observation["rate_limited"] = any(
            status in (429, 423) for status in statuses
        )
        if not observation["rate_limited"]:
            observation["vuln_category"] = "AuthenticationExposure"
            observation["replay_proof"] = {
                "attempts": len(statuses),
                "statuses": statuses,
            }
        return (observation,)

    def _csrf(
        self,
        base: str,
        username: str,
        password: str,
        paths: dict[str, str],
    ) -> tuple[dict[str, Any], ...]:
        observation: dict[str, Any] = {
            "kind": "csrf_password_change",
            "endpoint": f"{base}{paths['csrf']}",
            "method": "GET",
        }
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
                login_path=paths["login"],
                login_fields=(
                    dict(paths["login_fields"])
                    if paths.get("login_fields")
                    else None
                ),
            ):
                observation["login"] = "failed"
                return (observation,)
            _set_security_low(
                client,
                security_path=paths["security"],
                enabled=paths.get("set_security", True),
            )
            response = client.get(
                paths["csrf"],
                params={
                    "password_new": "password",
                    "password_conf": "password",
                    "Change": "Change",
                },
            )
            observation["status"] = response.status_code
            observation["changed_without_token"] = (
                "Password Changed" in response.text
                or "password has been changed" in response.text
            )
            if observation["changed_without_token"]:
                observation["vuln_category"] = "CSRF"
                observation["replay_proof"] = {
                    "endpoint": paths["csrf"],
                    "status": response.status_code,
                }
        return (observation,)

    def _weak_session(
        self,
        base: str,
        username: str,
        password: str,
        paths: dict[str, str],
    ) -> tuple[dict[str, Any], ...]:
        observation: dict[str, Any] = {
            "kind": "weak_session",
            "endpoint": f"{base}{paths['weak_id']}",
        }
        ids: list[str] = []
        page_ids: list[str] = []
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
                login_path=paths["login"],
                login_fields=(
                    dict(paths["login_fields"])
                    if paths.get("login_fields")
                    else None
                ),
            ):
                observation["login"] = "failed"
                return (observation,)
            _set_security_low(
                client,
                security_path=paths["security"],
                enabled=paths.get("set_security", True),
            )
            for _ in range(2):
                response = client.post(paths["weak_id"])
                value = client.cookies.get("dvwaSession")
                if value:
                    ids.append(value)
                match = re.search(r"Your ID is\s+(\d+)", response.text)
                if match:
                    page_ids.append(match.group(1))
        observation["session_ids"] = ids
        observation["page_ids"] = page_ids
        numeric = [int(value) for value in ids if value.isdigit()]
        if not numeric:
            numeric = [int(value) for value in page_ids if value.isdigit()]
        observation["sequential"] = (
            len(numeric) >= 2 and numeric[-1] == numeric[0] + 1
        )
        if observation["sequential"]:
            observation["vuln_category"] = "SessionManagement"
            observation["replay_proof"] = {
                "session_ids": ids,
                "page_ids": page_ids,
            }
        return (observation,)

    def _info_disclosure(
        self,
        base: str,
        username: str,
        password: str,
        paths: dict[str, str],
    ) -> tuple[dict[str, Any], ...]:
        observations: list[dict[str, Any]] = []
        with httpx.Client(
            base_url=base,
            timeout=self._timeout,
            verify=False,
            trust_env=False,
            follow_redirects=True,
        ) as client:
            for path in (
                paths["setup"],
                paths["phpids"],
                paths["config_dir"],
                paths["uploads_dir"],
            ):
                response = client.get(path)
                listed = "Index of" in response.text
                observation: dict[str, Any] = {
                    "kind": "info_disclosure",
                    "endpoint": f"{base}{path}",
                    "status": response.status_code,
                    "directory_listing": listed,
                }
                if response.status_code == 200 and (
                    listed
                    or "PHP version" in response.text
                    or "phpids" in response.text.lower()
                ):
                    observation["vuln_category"] = (
                        "DirectoryBrowsing"
                        if listed
                        else "InformationDisclosure"
                    )
                    observation["replay_proof"] = {
                        "path": path,
                        "status": response.status_code,
                        "listed": listed,
                    }
                observations.append(observation)
            if _login(
                client,
                username=username,
                password=password,
                login_path=paths["login"],
                login_fields=(
                    dict(paths["login_fields"])
                    if paths.get("login_fields")
                    else None
                ),
            ):
                phpinfo = client.get(paths["phpinfo"])
                if phpinfo.status_code == 200 and "PHP Version" in (
                    phpinfo.text
                ):
                    observations.append(
                        {
                            "kind": "info_disclosure",
                            "endpoint": f"{base}{paths['phpinfo']}",
                            "status": 200,
                            "directory_listing": False,
                            "vuln_category": "InformationDisclosure",
                            "replay_proof": {
                                "path": paths["phpinfo"],
                                "status": 200,
                            },
                        }
                    )
        return tuple(observations)

    def _security_headers(
        self,
        base: str,
        paths: dict[str, str],
    ) -> tuple[dict[str, Any], ...]:
        required_headers = (
            "content-security-policy",
            "strict-transport-security",
            "x-frame-options",
            "x-content-type-options",
            "referrer-policy",
            "permissions-policy",
        )
        with httpx.Client(
            base_url=base,
            timeout=self._timeout,
            verify=False,
            trust_env=False,
            follow_redirects=True,
        ) as client:
            response = client.get(paths["login"])
        missing = [
            header
            for header in required_headers
            if response.headers.get(header) is None
        ]
        set_cookie = response.headers.get("set-cookie", "")
        cookie_flags = {
            flag: flag.lower() in set_cookie.lower()
            for flag in ("HttpOnly", "Secure", "SameSite")
        }
        observations: list[dict[str, Any]] = []
        if missing:
            observations.append(
                {
                    "kind": "missing_security_headers",
                    "endpoint": f"{base}{paths['login']}",
                    "missing": missing,
                    "vuln_category": "HeaderSecurity",
                    "replay_proof": {
                        "endpoint": paths["login"],
                        "missing": missing,
                    },
                }
            )
        if not all(cookie_flags.values()):
            observations.append(
                {
                    "kind": "insecure_cookie_flags",
                    "endpoint": f"{base}{paths['login']}",
                    "cookie_flags": cookie_flags,
                    "vuln_category": "CookieSecurity",
                    "replay_proof": {
                        "endpoint": paths["login"],
                        "cookie_flags": cookie_flags,
                    },
                }
            )
        return tuple(observations)
