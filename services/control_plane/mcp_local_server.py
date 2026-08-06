from __future__ import annotations

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("veridix-local")


@mcp.tool()
def veridix_tool_catalog() -> dict:
    """List the security tool packs and their capabilities."""
    return {
        "packs": ["network", "web", "vulnscan", "host", "binary", "code"],
        "tools": [
            "nmap.scan",
            "nuclei.scan",
            "web.sqlmap.scan",
            "web.nikto.scan",
            "web.dirb.scan",
            "web.wpscan.scan",
            "host.auth.hydra",
            "code.sast.semgrep",
        ],
    }


@mcp.tool()
def veridix_status() -> dict:
    """Return a lightweight health summary of the local agent stack."""
    return {
        "storage": ["pgvector", "qdrant", "chroma", "neo4j"],
        "embedding": "nomic-embed-text",
        "rerank": "BAAI/bge-reranker-base",
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
