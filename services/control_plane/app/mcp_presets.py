from __future__ import annotations

from pathlib import Path


MCP_PRESETS = [
    {
        "id": "veridix-local",
        "name": "Veridix Local",
        "kind": "local",
        "command": (
            "python -m services.control_plane.mcp_local_server"
        ),
        "description": "仓库内置 MCP 服务，暴露 veridix 工具目录与状态",
        "env_hint": {},
    },
    {
        "id": "filesystem",
        "name": "Filesystem",
        "kind": "local",
        "command": (
            "npx -y @modelcontextprotocol/server-filesystem "
            + str(Path(__file__).resolve().parents[3])
        ),
        "description": "读写本地文件系统，适合源码与资产文件处理",
        "env_hint": {},
    },
    {
        "id": "fetch",
        "name": "Fetch",
        "kind": "local",
        "command": "npx --no-install mcp-fetch-server",
        "description": "抓取 URL 内容，使用本地 mcp-fetch-server",
        "env_hint": {},
    },
    {
        "id": "sequential-thinking",
        "name": "Sequential Thinking",
        "kind": "local",
        "command": "npx -y @modelcontextprotocol/server-sequential-thinking",
        "description": "逐步结构化推理，适合复杂漏洞链分析",
        "env_hint": {},
    },
    {
        "id": "playwright",
        "name": "Playwright",
        "kind": "local",
        "command": "npx -y @playwright/mcp@latest",
        "description": "浏览器自动化，可辅助 Web 漏洞验证",
        "env_hint": {},
    },
    {
        "id": "memory",
        "name": "Memory",
        "kind": "local",
        "command": "npx -y @modelcontextprotocol/server-memory",
        "description": "跨会话持久化记忆，适合资产与线索累积",
        "env_hint": {},
    },
    {
        "id": "context7",
        "name": "Context7",
        "kind": "local",
        "command": "npx -y @upstash/context7-mcp",
        "description": "按需检索最新框架/组件文档",
        "env_hint": {},
    },
    {
        "id": "github",
        "name": "GitHub",
        "kind": "local",
        "command": "npx -y @modelcontextprotocol/server-github",
        "description": "仓库、Issue、PR、代码检索",
        "env_hint": {"GITHUB_PERSONAL_ACCESS_TOKEN": "需要 GitHub Token"},
    },
    {
        "id": "brave-search",
        "name": "Brave Search",
        "kind": "local",
        "command": "npx -y @modelcontextprotocol/server-brave-search",
        "description": "真实网络搜索，用于资产与情报收集",
        "env_hint": {"BRAVE_API_KEY": "需要 Brave API Key"},
    },
]


def list_mcp_presets() -> list[dict]:
    return [dict(item) for item in MCP_PRESETS]
