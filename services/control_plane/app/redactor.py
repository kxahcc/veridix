from __future__ import annotations

import re
from typing import Any

from .contracts import AgentEvent

SENSITIVE_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api-key",
    "password",
    "token",
}

SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "api_key",
    "apikey",
    "password",
    "secret",
    "token",
    "credential",
    "private_key",
}

BODY_SECRET_PATTERN = re.compile(
    r"(?i)((?:token|password|secret|api[_-]?key)[\"']?\s*[:=]\s*[\"']?)[^,\s}\"']+"
)


class Redactor:
    def redact_text(self, text: str) -> str:
        return BODY_SECRET_PATTERN.sub(r"\1[REDACTED]", text)

    def redact_headers(self, headers: dict[str, str]) -> dict[str, str]:
        return {
            key: (
                f"[REDACTED:{key.lower()}]"
                if key.lower() in SENSITIVE_HEADERS and value
                else value
            )
            for key, value in headers.items()
        }

    def redact_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: self.redact_key_value(key, item) for key, item in value.items()
            }
        if isinstance(value, list):
            return [self.redact_value(item) for item in value]
        if isinstance(value, str):
            return self.redact_text(value)
        return value

    def redact_key_value(self, key: str, value: Any) -> Any:
        if isinstance(value, str) and key.lower() in SENSITIVE_KEYS and value:
            return f"[REDACTED:{key.lower()}]"
        return self.redact_value(value)

    def redact_payload(self, payload: dict) -> dict:
        redacted = self.redact_value(payload)
        return redacted if isinstance(redacted, dict) else {"value": redacted}

    def redact_event(self, event: AgentEvent) -> AgentEvent:
        return event.model_copy(update={"payload": self.redact_payload(event.payload)})
