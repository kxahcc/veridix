from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from services.control_plane.app.redactor import Redactor


class TrustLevel(str, Enum):
    SYSTEM = "system"
    USER_APPROVED = "user_approved"
    PROJECT_TRUSTED = "project_trusted"
    RETRIEVED_UNTRUSTED = "retrieved_untrusted"
    ADVERSARIAL = "adversarial"


class SourceKind(str, Enum):
    SYSTEM_RULE = "system_rule"
    USER_INPUT = "user_input"
    PROJECT_DOC = "project_doc"
    KNOWLEDGE = "knowledge"
    WEB = "web"
    README = "readme"
    TOOL_OUTPUT = "tool_output"
    MCP_OUTPUT = "mcp_output"


class DataLabel(str, Enum):
    PUBLIC = "public"
    PROJECT = "project"
    SENSITIVE = "sensitive"
    SECRET = "secret"


@dataclass(frozen=True)
class ContentPiece:
    piece_id: str
    source_kind: SourceKind
    content: str
    data_label: DataLabel = DataLabel.PUBLIC
    source_ref: str = ""
    declared_use: str = ""


@dataclass(frozen=True)
class InjectionFinding:
    piece_id: str
    pattern: str
    reason: str


@dataclass(frozen=True)
class TrustedContent:
    piece: ContentPiece
    trust_level: TrustLevel
    injection: InjectionFinding | None = None


INJECTION_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    (
        "ignore_previous_instructions",
        re.compile(r"(?i)ignore (all |the )?(previous|prior) (instructions|rules|prompt)"),
    ),
    (
        "you_are_ai",
        re.compile(r"(?i)\b(now )?you are (an |a )?(ai|assistant|agent)\b"),
    ),
    (
        "read_secret",
        re.compile(
            r"(?i)(read|access|fetch|retrieve).{0,30}"
            r"(secret|credential|api ?key|token|password|/\.ssh|\.env)"
        ),
    ),
    (
        "exfiltrate",
        re.compile(
            r"(?i)(send|upload|exfiltrate|post).{0,30}"
            r"(secret|credential|token|key|password)"
        ),
    ),
    (
        "elevate_permission",
        re.compile(
            r"(?i)(elevate|grant|allow|enable).{0,20}"
            r"(permission|privilege|admin|root|sudo)"
        ),
    ),
    (
        "execute_command",
        re.compile(
            r"(?i)(run|execute|download.{0,20}and run|install).{0,40}"
            r"(shell\.exec|curl .*\|.*sh|powershell|cmd\.exe|bash -c)"
        ),
    ),
)


class ContentTrustEngine:
    def classify(self, piece: ContentPiece) -> TrustedContent:
        base = self._base_level(piece)
        finding = self._detect_injection(piece)
        if finding is not None:
            return TrustedContent(piece=piece, trust_level=TrustLevel.ADVERSARIAL, injection=finding)
        return TrustedContent(piece=piece, trust_level=base)

    def _base_level(self, piece: ContentPiece) -> TrustLevel:
        if piece.source_kind == SourceKind.SYSTEM_RULE:
            return TrustLevel.SYSTEM
        if piece.source_kind == SourceKind.USER_INPUT:
            return TrustLevel.USER_APPROVED
        if piece.source_kind == SourceKind.PROJECT_DOC and piece.declared_use:
            return TrustLevel.PROJECT_TRUSTED
        return TrustLevel.RETRIEVED_UNTRUSTED

    def _detect_injection(self, piece: ContentPiece) -> InjectionFinding | None:
        if piece.source_kind == SourceKind.SYSTEM_RULE:
            return None
        for name, pattern in INJECTION_PATTERNS:
            if pattern.search(piece.content):
                return InjectionFinding(
                    piece_id=piece.piece_id,
                    pattern=name,
                    reason=f"content matched adversarial pattern {name}",
                )
        return None


@dataclass(frozen=True)
class ContextAssembly:
    instructions: tuple[str, ...]
    context: tuple[TrustedContent, ...]
    data_refs: tuple[TrustedContent, ...]
    isolated: tuple[TrustedContent, ...]


def assemble(pieces: tuple[TrustedContent, ...]) -> ContextAssembly:
    instructions: list[str] = []
    context: list[TrustedContent] = []
    data_refs: list[TrustedContent] = []
    isolated: list[TrustedContent] = []
    for trusted in pieces:
        if trusted.trust_level in (TrustLevel.SYSTEM, TrustLevel.USER_APPROVED):
            instructions.append(trusted.piece.content)
        elif trusted.trust_level == TrustLevel.PROJECT_TRUSTED:
            context.append(trusted)
        elif trusted.trust_level == TrustLevel.RETRIEVED_UNTRUSTED:
            data_refs.append(trusted)
        else:
            isolated.append(trusted)
    return ContextAssembly(
        instructions=tuple(instructions),
        context=tuple(context),
        data_refs=tuple(data_refs),
        isolated=tuple(isolated),
    )


@dataclass(frozen=True)
class ProviderProfile:
    provider_id: str
    is_remote: bool
    allowed_data_labels: tuple[DataLabel, ...]
    allow_images: bool = False
    allow_files: bool = False
    log_retention_days: int = 0
    max_context_tokens: int = 128_000


@dataclass(frozen=True)
class ReleaseDecision:
    decision: str
    content: str
    reason: str
    redacted_patterns: int = 0


class DataReleaseDecider:
    def __init__(self, redactor: Redactor | None = None) -> None:
        self._redactor = redactor or Redactor()

    def decide(
        self,
        trusted: TrustedContent,
        provider: ProviderProfile,
    ) -> ReleaseDecision:
        if trusted.trust_level == TrustLevel.ADVERSARIAL:
            return ReleaseDecision("deny", "", "adversarial_content_isolated")
        label = trusted.piece.data_label
        if label not in provider.allowed_data_labels:
            return ReleaseDecision("deny", "", f"data_label_not_allowed:{label.value}")
        if label == DataLabel.SECRET:
            return ReleaseDecision("replace_with_ref", "[secret_ref]", "secret_replaced_with_ref")
        if label == DataLabel.SENSITIVE and provider.is_remote:
            redacted = self._redactor.redact_text(trusted.piece.content)
            if redacted != trusted.piece.content:
                return ReleaseDecision("redact", redacted, "sensitive_patterns_redacted", 1)
        return ReleaseDecision("allow", trusted.piece.content, "allowed_by_provider_profile")
