import { spawn, type ChildProcess } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { appendFileSync } from "node:fs";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";

export interface SupervisorOptions {
  rootDir: string;
  runtimeDir: string;
  controlCommand: string[];
  agentCommand: string[];
  controlHealthUrl: string;
  agentHeartbeatFile: string;
  heartbeatStaleMs?: number;
  maxRestarts?: number;
  crashWindowMs?: number;
  startupTimeoutMs?: number;
  waitForControl?: () => Promise<void>;
  waitForAgent?: () => Promise<void>;
}

interface ManagedProcess {
  name: string;
  command: string[];
  child: ChildProcess | null;
  pid: number | null;
  restarts: number;
  startedAt: number | null;
  lastExitAt: number | null;
  status: "running" | "stopped" | "crash_loop";
}

export interface SupervisorStatus {
  processes: Record<string, { status: string; restarts: number; pid: number | null }>;
  workerStatus: "ok" | "worker_lost" | "worker_crash_loop" | "stopped";
  updatedAt: string;
}

export class Supervisor {
  private readonly options: Required<
    Pick<
      SupervisorOptions,
      "maxRestarts" | "crashWindowMs" | "startupTimeoutMs"
    >
  > &
    SupervisorOptions;
  private processes: ManagedProcess[] = [];
  private stopping = false;
  private readonly statePath: string;

  constructor(options: SupervisorOptions) {
    this.options = {
      maxRestarts: 3,
      crashWindowMs: 30_000,
      startupTimeoutMs: 20_000,
      heartbeatStaleMs: 10_000,
      ...options,
    };
    this.statePath = path.join(options.runtimeDir, "state", "supervisor.json");
  }

  async start(): Promise<SupervisorStatus> {
    mkdirSync(path.dirname(this.statePath), { recursive: true });
    this.stopping = false;
    await this.assertNotRunning();
    this.processes = [
      {
        name: "control-plane",
        command: this.options.controlCommand,
        child: null,
        pid: null,
        restarts: 0,
        startedAt: null,
        lastExitAt: null,
        status: "stopped",
      },
      {
        name: "agent-worker",
        command: this.options.agentCommand,
        child: null,
        pid: null,
        restarts: 0,
        startedAt: null,
        lastExitAt: null,
        status: "stopped",
      },
    ];
    for (const process of this.processes) {
      this.spawnManaged(process);
    }
    try {
      await this.waitFor(
        this.options.waitForControl ?? (() => this.waitForHttp(this.options.controlHealthUrl)),
      );
      await this.waitFor(
        this.options.waitForAgent ?? (() => this.waitForFile(this.options.agentHeartbeatFile)),
      );
    } catch (error) {
      await this.stop();
      throw error;
    }
    for (const managed of this.processes) {
      if (managed.status !== "running" || managed.child === null) {
        await this.stop();
        throw new Error(
          `${managed.name} exited during startup; see runtime/logs/supervisor.jsonl`,
        );
      }
    }
    return this.status();
  }

  private async assertNotRunning() {
    try {
      const response = await fetch(this.options.controlHealthUrl, {
        signal: AbortSignal.timeout(1500),
      });
      if (response.ok) {
        throw new Error(
          `control plane already running at ${this.options.controlHealthUrl}; run veridix down first`,
        );
      }
    } catch (error) {
      if (error instanceof TypeError) {
        return;
      }
      throw error;
    }
  }

  async stop(): Promise<SupervisorStatus> {
    this.stopping = true;
    if (this.processes.length === 0) {
      this.loadFromState();
    }
    for (const managed of this.processes) {
      if (managed.child && !managed.child.killed) {
        managed.child.kill("SIGTERM");
      } else if (managed.pid !== null) {
        try {
          process.kill(managed.pid, "SIGTERM");
        } catch {
          // process already gone
        }
      }
    }
    await delay(500);
    for (const managed of this.processes) {
      if (managed.child && managed.child.exitCode === null) {
        managed.child.kill("SIGKILL");
      } else if (managed.pid !== null) {
        try {
          process.kill(managed.pid, "SIGKILL");
        } catch {
          // process already gone
        }
      }
    }
    for (const managed of this.processes) {
      managed.status = "stopped";
      managed.pid = null;
    }
    return this.status();
  }

  status(): SupervisorStatus {
    const processes: SupervisorStatus["processes"] = {};
    let workerStatus: SupervisorStatus["workerStatus"] = "stopped";
    for (const process of this.processes) {
      processes[process.name] = {
        status: process.status,
        restarts: process.restarts,
        pid: process.pid,
      };
      if (process.name === "agent-worker" && process.status === "crash_loop") {
        workerStatus = "worker_crash_loop";
      } else if (
        process.name === "agent-worker" &&
        process.status === "running"
      ) {
        workerStatus = this.isHeartbeatFresh() ? "ok" : "worker_lost";
      } else if (process.name === "agent-worker" && process.status === "stopped") {
        workerStatus = this.stopping ? "stopped" : "worker_lost";
      }
    }
    const status: SupervisorStatus = {
      processes,
      workerStatus,
      updatedAt: new Date().toISOString(),
    };
    writeFileSync(this.statePath, `${JSON.stringify(status, null, 2)}\n`);
    return status;
  }

  readState(): SupervisorStatus | null {
    if (!existsSync(this.statePath)) {
      return null;
    }
    return JSON.parse(readFileSync(this.statePath, "utf8"));
  }

  private spawnManaged(managed: ManagedProcess) {
    const child = spawn(managed.command[0], managed.command.slice(1), {
      cwd: this.options.rootDir,
      env: {
        ...process.env,
        VERIDIX_RUNTIME_DIR: this.options.runtimeDir,
        VERIDIX_CONTROL_URL: "http://127.0.0.1:8787",
      },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    managed.child = child;
    managed.pid = child.pid ?? null;
    managed.startedAt = Date.now();
    managed.lastExitAt = null;
    managed.status = "running";
    child.stdout?.on("data", (chunk) => this.log(managed.name, chunk));
    child.stderr?.on("data", (chunk) => this.log(managed.name, chunk));
    child.on("exit", () => this.handleExit(managed));
  }

  private log(name: string, chunk: Buffer) {
    const logDir = path.join(this.options.runtimeDir, "logs");
    mkdirSync(logDir, { recursive: true });
    appendFileSync(
      path.join(logDir, "supervisor.jsonl"),
      `${JSON.stringify({ at: new Date().toISOString(), process: name, line: chunk.toString() })}\n`,
    );
  }

  private handleExit(managed: ManagedProcess) {
    managed.child = null;
    managed.pid = null;
    managed.lastExitAt = Date.now();
    if (this.stopping) {
      managed.status = "stopped";
      this.status();
      return;
    }
    const startedAt = managed.startedAt ?? managed.lastExitAt;
    const withinWindow =
      managed.lastExitAt !== null &&
      startedAt !== null &&
      managed.lastExitAt - startedAt <= this.options.crashWindowMs;
    if (managed.restarts < this.options.maxRestarts && withinWindow) {
      managed.restarts += 1;
      this.spawnManaged(managed);
    } else {
      managed.status = "crash_loop";
    }
    this.status();
  }

  private async waitFor(check: () => Promise<void>) {
    const deadline = Date.now() + this.options.startupTimeoutMs;
    while (Date.now() < deadline) {
      try {
        await check();
        return;
      } catch {
        await delay(250);
      }
    }
    throw new Error("supervisor startup timed out");
  }

  private async waitForHttp(url: string) {
    const response = await fetch(url, {
      signal: AbortSignal.timeout(2000),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
  }

  private async waitForFile(file: string) {
    if (!existsSync(file)) {
      throw new Error(`missing ${file}`);
    }
  }

  private isHeartbeatFresh(): boolean {
    try {
      const stat = statSync(this.options.agentHeartbeatFile);
      return (
        Date.now() - stat.mtimeMs <= (this.options.heartbeatStaleMs ?? 10_000)
      );
    } catch {
      return false;
    }
  }

  private loadFromState() {
    const state = this.readState();
    if (!state) {
      return;
    }
    this.processes = [
      {
        name: "control-plane",
        command: this.options.controlCommand,
        child: null,
        pid: state.processes["control-plane"]?.pid ?? null,
        restarts: state.processes["control-plane"]?.restarts ?? 0,
        startedAt: null,
        lastExitAt: null,
        status: "stopped",
      },
      {
        name: "agent-worker",
        command: this.options.agentCommand,
        child: null,
        pid: state.processes["agent-worker"]?.pid ?? null,
        restarts: state.processes["agent-worker"]?.restarts ?? 0,
        startedAt: null,
        lastExitAt: null,
        status: "stopped",
      },
    ];
  }
}
