#!/usr/bin/env python
"""Start the local dev stack for manual testing.

Starts lab provider, control plane, agent worker (fake runner) and the
Web dev server, then writes PIDs and URLs to runtime/dev-stack.json.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
LAB_PORT = int(os.environ.get("VERIDIX_LAB_PORT", "8766"))
CONTROL_PORT = int(os.environ.get("VERIDIX_CONTROL_PORT", "8787"))
WEB_PORT = int(os.environ.get("VERIDIX_WEB_PORT", "5173"))
AGENT_PORT = int(os.environ.get("VERIDIX_AGENT_PORT", str(CONTROL_PORT + 1)))
LAB_URL = f"http://127.0.0.1:{LAB_PORT}"
CONTROL_URL = f"http://127.0.0.1:{CONTROL_PORT}"
WEB_URL = f"http://127.0.0.1:{WEB_PORT}"


def _popen(args: list[str], env: dict[str, str], name: str) -> subprocess.Popen:
    out = open(RUNTIME / f"{name}.out", "w", encoding="utf-8")
    err = open(RUNTIME / f"{name}.err", "w", encoding="utf-8")
    return subprocess.Popen(
        args,
        cwd=str(ROOT),
        env=env,
        stdout=out,
        stderr=err,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _wait(url: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=1.5, trust_env=False).status_code < 500:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"did not become ready: {url}")


def _stop_process_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            check=False,
            timeout=30,
        )
    else:
        proc.terminate()


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--stop":
        path = RUNTIME / "dev-stack.json"
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            for key in (
                "lab_pid",
                "control_pid",
                "worker_pid",
                "web_pid",
            ):
                pid = payload.get(key)
                if pid:
                    try:
                        subprocess.run(
                            [
                                "taskkill",
                                "/PID",
                                str(int(pid)),
                                "/F",
                            ],
                            capture_output=True,
                            check=False,
                        )
                    except Exception:
                        pass
        cleanup_script = (
            "$root = '" + str(ROOT) + "'; "
            "$ports = @(" + str(LAB_PORT) + ", " +
            str(CONTROL_PORT) + ", " + str(AGENT_PORT) +
            ", " + str(WEB_PORT) + "); "
            "foreach ($port in $ports) { "
            "  $listener = Get-NetTCPConnection -State Listen -"
            "LocalPort $port -ErrorAction SilentlyContinue | "
            "Select-Object -First 1; "
            "  if ($listener) { "
            "    Stop-Process -Id $listener.OwningProcess -Force -"
            "ErrorAction SilentlyContinue } "
            "} "
            "$targets = Get-CimInstance Win32_Process | Where-Object { "
            "$_.CommandLine -like ('*' + $root + '*') -and "
            "$_.Name -in @('python.exe','node.exe','cmd.exe') }; "
            "foreach ($p in $targets) { "
            "Stop-Process -Id $p.ProcessId -Force -ErrorAction "
            "SilentlyContinue }"
        )
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                cleanup_script,
            ],
            capture_output=True,
            check=False,
        )
        print("dev stack stopped", flush=True)
        return 0
    RUNTIME.mkdir(parents=True, exist_ok=True)
    venv_python = ROOT / ".venv" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    python = (
        os.environ.get("VERIDIX_PYTHON")
        or (str(venv_python) if venv_python.exists() else sys.executable)
    )
    base_env = {
        **os.environ,
        "VERIDIX_RUNTIME_DIR": str(RUNTIME),
        "VERIDIX_CONTROL_DB": str(RUNTIME / "control.sqlite3"),
        "VERIDIX_CONTROL_PORT": str(CONTROL_PORT),
        "VERIDIX_AGENT_PORT": str(AGENT_PORT),
        "VERIDIX_CONTROL_URL": CONTROL_URL,
        "VERIDIX_CONTROL_URL": CONTROL_URL,
        "VITE_CONTROL_URL": CONTROL_URL,
        "VERIDIX_LAB_PORT": str(LAB_PORT),
        "VERIDIX_WEB_PORT": str(WEB_PORT),
        "VERIDIX_WORKER_AUTOPILOT": "1",
        "VERIDIX_PROVIDER_ENDPOINT": f"{LAB_URL}/v1",
        "VERIDIX_PROVIDER_MODEL": "veridix-lab-flash",
        "VERIDIX_ZAP_URL": os.environ.get(
            "VERIDIX_ZAP_URL",
            "http://127.0.0.1:8090",
        ),
        "VERIDIX_ZAP_API_KEY": os.environ.get(
            "VERIDIX_ZAP_API_KEY",
            "veridix-zap",
        ),
        "VERIDIX_RUNNER": os.environ.get("VERIDIX_RUNNER", "fake"),
        "VERIDIX_TOOL_NETWORK": os.environ.get(
            "VERIDIX_TOOL_NETWORK",
            "veridix-system_veridix-net",
        ),
        "VERIDIX_MEMORY_DB": str(RUNTIME / "memory.db"),
        "VERIDIX_STORAGE_PROFILE": "server",
        "VERIDIX_STORAGE_AUTOPROVISION": "1",
        "VERIDIX_STORAGE_REGISTRY": "docker.m.daocloud.io/",
        "VERIDIX_PGVECTOR_PORT": "55432",
        "VERIDIX_PGVECTOR_URL": (
            "postgresql://veridix:veridix@127.0.0.1:55432/veridix"
        ),
        "VERIDIX_QDRANT_URL": "http://127.0.0.1:6333",
        "VERIDIX_VECTOR_BACKEND": "qdrant",
        "VERIDIX_CHROMA_URL": "http://127.0.0.1:8001",
        "VERIDIX_NEO4J_URI": "bolt://127.0.0.1:7687",
        "VERIDIX_NEO4J_USER": "neo4j",
        "VERIDIX_NEO4J_PASSWORD": "veridixpass",
        "VERIDIX_EMBEDDING_ENDPOINT": "http://127.0.0.1:11434/v1",
        "VERIDIX_EMBEDDING_MODEL": "nomic-embed-text",
        "VERIDIX_EMBEDDING_KEEP_ALIVE": "5m",
        "VERIDIX_RERANK_ENABLED": "1",
        "VERIDIX_RERANK_BACKEND": "fastembed",
        "VERIDIX_RERANK_MODEL": "BAAI/bge-reranker-base",
        "HF_ENDPOINT": "https://hf-mirror.com",
        "HF_HUB_DISABLE_XET": "1",
        "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
    }
    local_env = ROOT / ".env.local"
    if local_env.exists():
        for line in local_env.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if (
                stripped
                and not stripped.startswith("#")
                and "=" in stripped
            ):
                key, _, value = stripped.partition("=")
                base_env[key.strip()] = value.strip().strip('"').strip("'")
    lab = _popen(
        [
            python,
            "-m",
            "services.lab_provider.app.main",
            "--host",
            "127.0.0.1",
            "--port",
            str(LAB_PORT),
        ],
        base_env,
        "lab",
    )
    control = _popen(
        [python, "-m", "services.control_plane.app.main"],
        base_env,
        "control",
    )
    worker = _popen(
        [python, "-m", "services.agent_runtime.app.main"],
        base_env,
        "worker",
    )
    web = _popen(
        [
            "npm.cmd",
            "--prefix",
            "apps/web",
            "run",
            "dev",
            "--",
              "--host",
              "127.0.0.1",
              "--port",
              str(WEB_PORT),
              "--force",
          ],
        base_env,
        "web",
    )
    try:
        _wait(f"{LAB_URL}/healthz", timeout=120.0)
        _wait(f"{CONTROL_URL}/healthz")
        _wait(WEB_URL)
    except RuntimeError:
        for proc in (lab, control, worker, web):
            _stop_process_tree(proc)
        raise
    payload = {
        "lab_pid": lab.pid,
        "control_pid": control.pid,
        "worker_pid": worker.pid,
        "web_pid": web.pid,
        "control_url": CONTROL_URL,
        "web_url": WEB_URL,
        "lab_url": LAB_URL,
        "provider": "veridix-lab-flash (mock)",
    }
    (RUNTIME / "dev-stack.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
