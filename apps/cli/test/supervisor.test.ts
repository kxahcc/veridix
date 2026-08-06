import { mkdtempSync, rmSync, utimesSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { Supervisor } from "../src/supervisor.js";

const tempDirs: string[] = [];

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

describe("supervisor", () => {
  it("marks agent-worker crash loop after bounded restarts", async () => {
    const tmp = mkdtempSync(path.join(os.tmpdir(), "veridix-sup-"));
    tempDirs.push(tmp);
    const runtimeDir = path.join(tmp, "runtime");
    const failing = [process.execPath, "-e", "process.exit(1)"];
    const supervisor = new Supervisor({
      rootDir: tmp,
      runtimeDir,
      controlCommand: failing,
      agentCommand: failing,
      controlHealthUrl: "http://127.0.0.1:1/healthz",
      agentHeartbeatFile: path.join(runtimeDir, "state", "agent-worker.heartbeat"),
      maxRestarts: 2,
      crashWindowMs: 1000,
      startupTimeoutMs: 1000,
      waitForControl: async () => {},
      waitForAgent: async () => {},
    });

    await supervisor.start();
    let status = supervisor.status();
    const deadline = Date.now() + 10_000;
    while (
      status.workerStatus !== "worker_crash_loop" &&
      Date.now() < deadline
    ) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      status = supervisor.status();
    }

    expect(status.workerStatus).toBe("worker_crash_loop");
    expect(status.processes["agent-worker"]?.restarts).toBe(2);
    await supervisor.stop();
  });

  it("reports worker_lost when the agent heartbeat is stale", async () => {
    const tmp = mkdtempSync(path.join(os.tmpdir(), "veridix-sup-"));
    tempDirs.push(tmp);
    const runtimeDir = path.join(tmp, "runtime");
    const heartbeat = path.join(runtimeDir, "state", "agent-worker.heartbeat");
    const running = [process.execPath, "-e", "setInterval(()=>{},1000)"];
    const supervisor = new Supervisor({
      rootDir: tmp,
      runtimeDir,
      controlCommand: running,
      agentCommand: running,
      controlHealthUrl: "http://127.0.0.1:1/healthz",
      agentHeartbeatFile: heartbeat,
      maxRestarts: 1,
      crashWindowMs: 1000,
      startupTimeoutMs: 1000,
      heartbeatStaleMs: 1000,
      waitForControl: async () => {},
      waitForAgent: async () => {},
    });

    await supervisor.start();
    writeFileSync(heartbeat, "{}", "utf8");
    const past = new Date(Date.now() - 5000);
    utimesSync(heartbeat, past, past);

    const status = supervisor.status();

    expect(status.workerStatus).toBe("worker_lost");
    await supervisor.stop();
  });
});
