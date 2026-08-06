"""Reusable task-level Loop Profile presets.

A preset is a named bundle of per-role overrides that makes the
profile-context mechanism directly usable from Web/TUI/CLI. Presets are
composable with explicit mission ``loop_profiles`` overrides: explicit user
values win over the preset defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LoopPreset:
    preset_id: str
    label: str
    description: str
    loop_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    compatible_templates: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "preset_id": self.preset_id,
            "label": self.label,
            "description": self.description,
            "loop_overrides": {
                role: dict(overrides)
                for role, overrides in self.loop_overrides.items()
            },
            "compatible_templates": list(self.compatible_templates),
        }


BUILTIN_LOOP_PRESETS: tuple[LoopPreset, ...] = (
    LoopPreset(
        preset_id="nikto-focused",
        label="Nikto Focus",
        description="把扫描角色收窄到 web-nikto 技能与 nikto 知识查询。",
        loop_overrides={
            "scanner": {
                "knowledge_query": (
                    "dvwa_nikto_exposure",
                    "nikto_web_server_scan",
                ),
                "allowed_skills": ("web-nikto",),
                "budget": {"tool_calls": 12},
            },
            "recon": {
                "allowed_skills": ("web-nikto",),
                "budget": {"tool_calls": 8},
            },
        },
        compatible_templates=("scanner_verify", "redteam_orchestration"),
    ),
    LoopPreset(
        preset_id="web-scan",
        label="Web Scan",
        description="通用 Web 扫描：保留多工具技能与 OWASP 知识查询。",
        loop_overrides={
            "scanner": {
                "knowledge_query": (
                    "web_application_scan",
                    "owasp_web_testing",
                ),
                "allowed_skills": (
                    "web-nikto",
                    "strix-nuclei",
                    "web-owasp",
                ),
                "budget": {"tool_calls": 30},
            }
        },
        compatible_templates=("scanner_verify", "redteam_orchestration"),
    ),
    LoopPreset(
        preset_id="code-audit",
        label="Code Audit",
        description="代码审计：聚焦 SAST 与源码狩猎技能。",
        loop_overrides={
            "code_scanner": {
                "knowledge_query": (
                    "code_audit",
                    "sast_rules",
                    "secret_detection",
                ),
                "allowed_skills": (
                    "strix-semgrep",
                    "cyberstrikeai-source-code-hunting",
                ),
                "budget": {"tool_calls": 30},
            },
            "code_verifier": {
                "knowledge_query": ("finding_verification", "source_evidence"),
                "allowed_skills": (
                    "verifier",
                    "cyberstrikeai-pentest-verification",
                ),
                "budget": {"tool_calls": 15},
            },
        },
        compatible_templates=("code_audit",),
    ),
    LoopPreset(
        preset_id="authz-matrix",
        label="Authz Matrix",
        description="授权矩阵：聚焦 IDOR 与业务规则知识。",
        loop_overrides={
            "authz_matrix": {
                "knowledge_query": (
                    "authz_oracles",
                    "business_rules",
                    "cwe_authz",
                ),
                "allowed_skills": (
                    "strix-idor",
                    "cyberstrike-idor-automation",
                ),
                "budget": {"tool_calls": 40},
            }
        },
        compatible_templates=("authz_matrix",),
    ),
    LoopPreset(
        preset_id="ssrf-callback",
        label="SSRF Callback",
        description="SSRF 回调：聚焦 OAST 与一次性 token 验证。",
        loop_overrides={
            "ssrf_callback": {
                "knowledge_query": ("ssrf_oracles", "oast_callback"),
                "allowed_skills": ("strix-ssrf", "cyberstrike-ssrf"),
                "budget": {"tool_calls": 25},
            }
        },
        compatible_templates=("ssrf_callback",),
    ),
    LoopPreset(
        preset_id="graphql",
        label="GraphQL",
        description="GraphQL 探测：聚焦 schema 与查询变异知识。",
        loop_overrides={
            "graphql": {
                "knowledge_query": (
                    "graphql_attack_surface",
                    "graphql_schema",
                ),
                "allowed_skills": (
                    "strix-graphql",
                    "cyberstrike-graphql",
                ),
                "budget": {"tool_calls": 30},
            }
        },
        compatible_templates=("graphql",),
    ),
    LoopPreset(
        preset_id="websocket",
        label="WebSocket",
        description="WebSocket 探测：聚焦实时授权与协议知识。",
        loop_overrides={
            "websocket": {
                "knowledge_query": ("websocket_protocol", "realtime_authz"),
                "allowed_skills": ("cyberstrike-websocket",),
                "budget": {"tool_calls": 25},
            }
        },
        compatible_templates=("websocket",),
    ),
    LoopPreset(
        preset_id="host-recon",
        label="Host Recon",
        description="主机侦察：聚焦端口、服务与主机后渗透知识。",
        loop_overrides={
            "recon": {
                "knowledge_query": (
                    "host_enumeration",
                    "service_validation",
                    "port_scan",
                ),
                "allowed_skills": (
                    "strix-nmap",
                    "host.enumeration",
                    "veridix-redteam-orchestration",
                ),
                "budget": {"tool_calls": 25},
            },
            "scanner": {
                "knowledge_query": (
                    "host_validation",
                    "service_validation",
                ),
                "allowed_skills": (
                    "strix-nmap",
                    "strix-nuclei",
                    "host.enumeration",
                ),
                "budget": {"tool_calls": 20},
            },
        },
        compatible_templates=("redteam_orchestration",),
    ),
    LoopPreset(
        preset_id="ad-attack",
        label="AD Attack",
        description="AD 攻击：聚焦 Kerberos、域枚举与后渗透路径。",
        loop_overrides={
            "recon": {
                "knowledge_query": (
                    "active_directory",
                    "domain_enumeration",
                    "kerberos",
                ),
                "allowed_skills": (
                    "cyberstrikeai-active-directory-attack",
                    "strix-active-directory",
                    "kerberos-attacks",
                ),
                "budget": {"tool_calls": 30},
            },
            "scanner": {
                "knowledge_query": (
                    "ad_kerberos",
                    "ldap_enumeration",
                    "lateral_movement",
                ),
                "allowed_skills": (
                    "cyberstrikeai-active-directory-attack",
                    "kerberos-attacks",
                    "windows-postexploit",
                ),
                "budget": {"tool_calls": 25},
            },
            "host": {
                "knowledge_query": (
                    "windows_postexploit",
                    "privilege_escalation",
                ),
                "allowed_skills": (
                    "windows-postexploit",
                    "kerberos-attacks",
                ),
                "budget": {"tool_calls": 20},
            },
        },
        compatible_templates=(),
    ),
    LoopPreset(
        preset_id="cloud-postexploit",
        label="Cloud Post-Exploit",
        description="云后渗透：聚焦 AWS/Azure 权限提升与凭据路径。",
        loop_overrides={
            "post_exploit": {
                "knowledge_query": (
                    "cloud_post_exploitation",
                    "iam_privilege_escalation",
                    "cloud_credentials",
                ),
                "allowed_skills": (
                    "aws-postexploit",
                    "azure-postexploit",
                    "k8s-postexploit",
                ),
                "budget": {"tool_calls": 25},
            },
            "host": {
                "knowledge_query": (
                    "cloud_asset_enumeration",
                    "container_escape",
                ),
                "allowed_skills": (
                    "aws-postexploit",
                    "azure-postexploit",
                    "k8s-postexploit",
                ),
                "budget": {"tool_calls": 20},
            },
        },
        compatible_templates=(),
    ),
)


class LoopPresetRegistry:
    def __init__(
        self,
        presets: tuple[LoopPreset, ...] | list[LoopPreset] | None = None,
    ) -> None:
        source = tuple(presets) if presets is not None else BUILTIN_LOOP_PRESETS
        self._presets = {preset.preset_id: preset for preset in source}

    def get(self, preset_id: str) -> LoopPreset | None:
        return self._presets.get(preset_id)

    def list(self) -> tuple[LoopPreset, ...]:
        return tuple(
            self._presets[key]
            for key in sorted(self._presets)
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            preset.preset_id: preset.as_dict()
            for preset in self.list()
        }


REGISTRY = LoopPresetRegistry()


def resolve_loop_profiles(
    *,
    preset_id: str | None,
    user_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Merge a preset with explicit per-role mission overrides."""
    user = dict(user_overrides or {})
    preset = REGISTRY.get(preset_id or "")
    if preset is None:
        return user
    merged: dict[str, dict[str, Any]] = {}
    for role in set(preset.loop_overrides) | set(user):
        base = dict(preset.loop_overrides.get(role) or {})
        base.update(user.get(role) or {})
        merged[role] = base
    return merged
