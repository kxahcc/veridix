from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("veridix-mock-mcp")


@mcp.tool()
def web_lookup(target: str, path: str = "/") -> dict:
    """Look up one endpoint on the authorized target."""
    return {"endpoint": f"{target}{path}", "status": 200}


if __name__ == "__main__":
    mcp.run(transport="stdio")
