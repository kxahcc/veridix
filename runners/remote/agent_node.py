#!/usr/bin/env python
"""Remote agent-node worker.

Polls the control plane for dispatched tasks, executes each task payload
locally, signs the result with a node keypair and posts it back. This closes
the dispatch -> execute -> signed-result loop for remote execution nodes.
The node registers with the `local-shell` capability; payloads may carry a
shell `command` or a known `tool` descriptor that is resolved into a command.

Usage:
  python -m runners.remote.agent_node \
    --control-url http://127.0.0.1:8787 \
    --node-id agent-node-1
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx

from .registry import RemoteNodeRegistry
from .signing import generate_keypair, sign_payload


TOOL_COMMAND_RESOLVER = {
    "nmap.scan": ["nmap", "-sV", "--open"],
    "nuclei.scan": ["nuclei", "-silent"],
    "fscan.scan": ["fscan", "-h"],
}


def _shell_probe_command(target: str) -> str:
    if os.name == "nt":
        return (
            "powershell -NoProfile -Command "
            f"\"try {{ (Invoke-WebRequest -UseBasicParsing {target} "
            f"-ErrorAction Stop).StatusCode }} catch {{ 000 }}\""
        )
    return f"curl -sS -o /dev/null -w '%{{http_code}}' {target}"


def _parse_payload(payload: dict) -> tuple[str, dict]:
    """Resolve a dispatch payload into (command, execution metadata)."""
    command = payload.get("command")
    if isinstance(command, list) and command:
        return " ".join(str(part) for part in command), {}
    if isinstance(command, str) and command.strip():
        return command, {}
    tool = payload.get("tool")
    args = payload.get("args") or {}
    target = str(args.get("target") or args.get("host") or "")
    if tool == "shell.probe":
        return _shell_probe_command(target), {"tool": tool, "args": args}
    if isinstance(tool, str) and tool in TOOL_COMMAND_RESOLVER:
        template = list(TOOL_COMMAND_RESOLVER[tool])
        if target:
            template.append(target)
        return " ".join(template), {"tool": tool, "args": args}
    if isinstance(tool, str):
        return f"echo unsupported-tool {tool}", {"tool": tool, "args": args}
    return "echo no-op", {}


def _execute_task(
    task: dict,
    *,
    node_id: str,
    private_key: str,
    client: httpx.Client,
) -> dict:
    payload = task.get("payload") or {}
    task_ref = str(task.get("task_ref") or "")
    command, metadata = _parse_payload(payload)
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            shell=True,
        )
        status = "completed" if proc.returncode == 0 else "failed"
        stdout = proc.stdout[-4000:]
        stderr = proc.stderr[-2000:]
    except subprocess.TimeoutExpired:
        status = "failed"
        stdout = ""
        stderr = "timeout"
    payload_body = {
        "stdout": stdout,
        "stderr": stderr,
        "command": command,
        **metadata,
    }
    signature = sign_payload(
        {
            "node_id": node_id,
            "task_ref": task_ref,
            "status": status,
            **payload_body,
        },
        private_key,
    )
    response = client.post(
        f"/api/v1/remote/nodes/{node_id}/results",
        json={
            "task_ref": task_ref,
            "status": status,
            "artifact_refs": [],
            "signature": signature,
            "payload": payload_body,
        },
    )
    response.raise_for_status()
    return {
        "task_ref": task_ref,
        "status": status,
        "signature": signature[:32] + "...",
        "stdout_len": len(stdout),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-url", default=os.environ.get("VERIDIX_CONTROL_URL", "http://127.0.0.1:8787"))
    parser.add_argument("--node-id", default=os.environ.get("VERIDIX_NODE_ID", "agent-node-1"))
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--once", action="store_true", help="process available tasks once and exit")
    parser.add_argument("--db", default="")
    args = parser.parse_args()

    registry = RemoteNodeRegistry(args.db or ":memory:")
    private_key, public_key = generate_keypair()
    with httpx.Client(base_url=args.control_url, timeout=15.0, trust_env=False) as client:
        client.post(
            "/api/v1/remote/nodes",
            json={
                "node_id": args.node_id,
                "version": args.version,
                "capabilities": ["local-shell"],
                "public_key": public_key,
            },
        )
        client.post(
            f"/api/v1/remote/nodes/{args.node_id}/heartbeat",
            json={"lease_seconds": 300},
        )

        while True:
            nodes = client.get("/api/v1/remote/nodes").json()
            node = next(
                (item for item in nodes if item.get("node_id") == args.node_id),
                None,
            )
            if node is not None:
                client.post(
                    f"/api/v1/remote/nodes/{args.node_id}/heartbeat",
                    json={"lease_seconds": 300},
                )

            tasks = client.get(
                f"/api/v1/remote/nodes/{args.node_id}/tasks"
            ).json()
            if tasks:
                for task in tasks:
                    summary = _execute_task(
                        task,
                        node_id=args.node_id,
                        private_key=private_key,
                        client=client,
                    )
                    print(json.dumps(summary), flush=True)
                if args.once:
                    return 0

            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
