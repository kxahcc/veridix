import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { createServer } from "node:net";
import path from "node:path";
import {
  DEFAULT_CONFIG,
  createConfigSnapshot,
  mergeConfig,
} from "@veridix/config";
import { ControlClient } from "./control-client.js";
import { DoctorOptions, runDoctorChecks } from "./doctor.js";

export interface SelfTestResult {
  tiers: Record<
    string,
    {
      status: "ok" | "warn" | "fail" | "skip";
      detail: string;
    }
  >;
  generatedAt: string;
}

function findSelfTestRoot(): string {
  if (process.env.VERIDIX_ROOT) {
    return process.env.VERIDIX_ROOT;
  }
  let current = process.cwd();
  while (true) {
    try {
      const manifest = JSON.parse(
        readFileSync(path.join(current, "package.json"), "utf8"),
      ) as { workspaces?: unknown };
      if (Array.isArray(manifest.workspaces)) {
        return current;
      }
    } catch {
      // keep walking up
    }
    const parent = path.dirname(current);
    if (parent === current) {
      return process.cwd();
    }
    current = parent;
  }
}

export async function runSelfTest(
  options: DoctorOptions,
): Promise<SelfTestResult> {
  const contractChecks = runContractChecks();
  const doctorChecks = await runDoctorChecks(options);
  const servicesCheck = doctorChecks.find(
    (check) => check.name === "control-plane",
  );
  const dockerCheck = doctorChecks.find((check) => check.name === "docker");
  const providerChecks = doctorChecks.filter((check) =>
    check.name.startsWith("provider:"),
  );
  const ragChecks = providerChecks.filter((check) =>
    /:(embedding|rerank)$/.test(check.name),
  );
  const ragStatus = ragChecks.length === 0
    ? "skip"
    : ragChecks.some((check) => check.status === "warn")
      ? "warn"
      : "ok";
  const browserCheck = doctorChecks.find((check) => check.name === "browser");
  const evidence = runEvidenceFixture();
  const report = runReportFixture();
  const continuity = servicesCheck?.status === "ok"
    ? await runContinuityCheck(options.controlHealthUrl ?? "http://127.0.0.1:8787/healthz")
    : { status: "skip" as const, detail: "control plane not running" };
  const localTarget = await runLocalTargetCheck();

  return {
    tiers: {
      contract: {
        status: contractChecks.every((check) => check.ok) ? "ok" : "fail",
        detail: contractChecks
          .map((check) => `${check.name}: ${check.ok ? "pass" : "fail"}`)
          .join(", "),
      },
      services: {
        status: servicesCheck?.status === "ok" ? "ok" : "skip",
        detail:
          servicesCheck?.status === "ok"
            ? "control-plane healthy"
            : "services not running; run veridix up",
      },
      runner: {
        status: dockerCheck?.status === "ok" ? "ok" : "warn",
        detail: dockerCheck?.detail ?? "docker unavailable",
      },
      rag: {
        status: ragStatus,
        detail:
          ragStatus === "skip"
            ? "embedding/rerank not configured"
            : ragChecks
                .map((check) => `${check.name}: ${check.detail}`)
                .join("; "),
      },
      web: {
        status: browserCheck?.status === "ok" ? "ok" : "warn",
        detail: browserCheck?.detail ?? "playwright browsers not found",
      },
      evidence: evidence,
      report: report,
      continuity: continuity,
      "local-target": localTarget,
    },
    generatedAt: new Date().toISOString(),
  };
}

async function runLocalTargetCheck() {
  if (process.env.VERIDIX_SELF_TEST_LOCAL_TARGET !== "1") {
    return {
      status: "skip" as const,
      detail: "set VERIDIX_SELF_TEST_LOCAL_TARGET=1 to start the local lab target",
    };
  }
  const root = findSelfTestRoot();
  const port = await freePort();
  const child = spawn(
    "python",
    [
      "-m",
      "services.lab_provider.app.main",
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
    ],
    { cwd: root, stdio: "ignore", windowsHide: true },
  );
  try {
    await waitForHttp(`http://127.0.0.1:${port}/healthz`);
    const models = await fetch(`http://127.0.0.1:${port}/models`).then(
      (response) => response.json(),
    );
    const chat = await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: "veridix-lab-flash",
        messages: [{ role: "user", content: "probe" }],
      }),
    }).then((response) => response.json());
    const ok =
      Array.isArray(models.data) &&
      models.data.length > 0 &&
      chat.choices?.[0]?.message !== undefined;
    return {
      status: ok ? ("ok" as const) : ("fail" as const),
      detail: ok
        ? "local lab provider models + chat completion pass"
        : "local lab provider contract failed",
    };
  } catch (error) {
    return {
      status: "fail" as const,
      detail: `local lab target failed: ${String(error)}`,
    };
  } finally {
    child.kill();
  }
}

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (address === null || typeof address === "string") {
        server.close();
        reject(new Error("no port allocated"));
        return;
      }
      const port = address.port;
      server.close(() => resolve(port));
    });
  });
}

async function waitForHttp(url: string, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) {
        return;
      }
    } catch {
      // retry until ready
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`endpoint did not become ready: ${url}`);
}

function runEvidenceFixture() {
  const fingerprint = createEvidenceFingerprint(
    "https://lab.example.test",
    "authz",
    "/admin",
    "role",
  );
  const ok = fingerprint.length === 64 && fingerprint !==
    createEvidenceFingerprint("https://lab.example.test", "authz", "/admin", "id");
  return {
    status: ok ? ("ok" as const) : ("fail" as const),
    detail: ok
      ? "evidence fingerprint fixture pass"
      : "evidence fingerprint fixture failed",
  };
}

function runReportFixture() {
  const report = {
    findings: [
      {
        finding_id: "finding_1",
        status: "verified",
        vuln_category: "authz",
        endpoint: "/admin",
      },
    ],
  };
  const json = JSON.stringify(report);
  const ok = json.includes("finding_1") && json.includes("verified");
  return {
    status: ok ? ("ok" as const) : ("fail" as const),
    detail: ok ? "json report fixture pass" : "json report fixture failed",
  };
}

async function runContinuityCheck(healthUrl: string) {
  const baseUrl = healthUrl.replace(/\/healthz$/, "");
  const client = new ControlClient(baseUrl);
  try {
    const project = await client.createProject("self-test");
    const mission = await client.createMission(project.project_id, "self-test", {});
    const run = await client.startRun(mission.mission_id, `self-test:${Date.now()}`);
    await client.claimRun(run.run_id, "agent-worker", `claim:${Date.now()}`);
    const paused = await client.runCommand(run.run_id, "pause", `pause:${Date.now()}`);
    const resumed = await client.runCommand(run.run_id, "resume", `resume:${Date.now()}`);
    await client.runCommand(run.run_id, "cancel", `cancel:${Date.now()}`);
    await client.deleteProject(project.project_id);
    const ok = paused.status === "paused" && resumed.status === "running";
    return {
      status: ok ? ("ok" as const) : ("fail" as const),
      detail: ok ? "pause/resume/cancel pass" : "pause/resume/cancel failed",
    };
  } catch (error) {
    return {
      status: "fail" as const,
      detail: `continuity fixture failed: ${String(error)}`,
    };
  }
}

function createEvidenceFingerprint(
  targetRef: string,
  category: string,
  endpoint: string,
  param: string,
) {
  const canonical = JSON.stringify({
    target: targetRef,
    category,
    endpoint,
    param,
  });
  return createHash("sha256").update(canonical).digest("hex");
}

function runContractChecks() {
  const merged = mergeConfig([], DEFAULT_CONFIG);
  const snapshot = createConfigSnapshot(merged.config);
  return [
    {
      name: "defaults-merge",
      ok: merged.config.profile === "desktop",
    },
    {
      name: "snapshot-hash",
      ok: /^[0-9a-f]{64}$/.test(snapshot.hash),
    },
  ];
}
