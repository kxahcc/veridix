from __future__ import annotations


BUILTIN_ROLE_TEMPLATES = [
    {
        "template_id": "scanner_verify",
        "label": "Scanner Verify",
        "description": "扫描器发起假设，验证角色复现并归档报告。",
        "builtin": True,
        "roles": ["scanner", "verifier", "reporter"],
    },
    {
        "template_id": "redteam_orchestration",
        "label": "Red Team Orchestration",
        "description": "侦察 -> 扫描 -> 验证 -> 报告的多阶段红队编排。",
        "builtin": True,
        "roles": ["recon", "scanner", "verifier", "reporter"],
    },
    {
        "template_id": "code_audit",
        "label": "Code Audit",
        "description": "SAST 与密钥扫描，验证结构化代码结果并归档报告。",
        "builtin": True,
        "roles": ["code_scanner", "code_verifier", "reporter"],
    },
    {
        "template_id": "authz_matrix",
        "label": "Authz Matrix",
        "description": "覆盖授权矩阵，验证越权与水平/垂直权限问题。",
        "builtin": True,
        "roles": ["discovery", "authz_checker", "reporter"],
    },
    {
        "template_id": "ssrf_callback",
        "label": "SSRF Callback",
        "description": "通过回调通道验证服务端请求伪造。",
        "builtin": True,
        "roles": ["ssrf_prober", "verifier", "reporter"],
    },
    {
        "template_id": "graphql",
        "label": "GraphQL",
        "description": "GraphQL 探测、schema 枚举与批量请求验证。",
        "builtin": True,
        "roles": ["graphql_discovery", "verifier", "reporter"],
    },
    {
        "template_id": "websocket",
        "label": "WebSocket",
        "description": "WebSocket 消息探测与协议级漏洞验证。",
        "builtin": True,
        "roles": ["ws_discovery", "verifier", "reporter"],
    },
    {
        "template_id": "webappsec",
        "label": "Web App Security",
        "description": "通用 Web 应用安全角色组合。",
        "builtin": True,
        "roles": ["discovery", "verifier", "reporter"],
    },
]


def list_builtin_templates() -> list[dict]:
    return [dict(item) for item in BUILTIN_ROLE_TEMPLATES]
