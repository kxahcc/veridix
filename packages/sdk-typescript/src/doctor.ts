import { execFile } from "node:child_process";
import { access, constants, mkdir } from "node:fs/promises";
import { readFileSync } from "node:fs";
import path from "node:path";
import { promisify } from "node:util";
import { probeConfiguredProviders, type ProviderConfig } from "./provider-probe.js";

const execFileAsync = promisify(execFile);

export interface DoctorCheck {
  name: string;
  status: "ok" | "warn" | "fail" | "skip";
  detail: string;
}

export interface DoctorOptions {
  runtimeDir: string;
  controlHealthUrl?: string;
  provider?: ProviderConfig;
}

export async function runDoctorChecks(
  options: DoctorOptions,
): Promise<DoctorCheck[]> {
  const checks: DoctorCheck[] = [];
  checks.push(checkNodeVersion());
  checks.push(await checkPythonVersion());
  checks.push(await checkDocker());
  checks.push(checkBrowserInstall());
  checks.push(await checkRuntimeDir(options.runtimeDir));
  checks.push(await checkControlPlane(options.controlHealthUrl));
  checks.push(await checkEnvironment(options.runtimeDir));
  checks.push(await checkConnectors(options.controlHealthUrl));
  const providerChecks = await probeConfiguredProviders(options.provider);
  if (providerChecks.length === 0) {
    checks.push({
      name: "provider",
      status: "skip",
      detail: "not configured; set provider.inference/embedding/rerank in config",
    });
  } else {
    checks.push(...providerChecks.map(checkProviderResult));
  }
  return checks;
}

function checkProviderResult(
  result: Awaited<ReturnType<typeof probeConfiguredProviders>>[number],
): DoctorCheck {
  if (result.status === "ok") {
    return {
      name: `provider:${result.providerId}:${result.kind}`,
      status: "ok",
      detail: result.detail,
    };
  }
  if (result.status === "unavailable") {
    return {
      name: `provider:${result.providerId}:${result.kind}`,
      status: "warn",
      detail: `${result.detail} (rag_degraded)`,
    };
  }
  return {
    name: `provider:${result.providerId}:${result.kind}`,
    status: "skip",
    detail: result.detail,
  };
}

function checkNodeVersion(): DoctorCheck {
  const [major] = process.versions.node.split(".").map(Number);
  if (major >= 22) {
    return { name: "node", status: "ok", detail: `Node ${process.versions.node}` };
  }
  return {
    name: "node",
    status: "fail",
    detail: `Node ${process.versions.node} is below the 22 LTS baseline`,
  };
}

async function checkPythonVersion(): Promise<DoctorCheck> {
  try {
    const { stdout } = await execFileAsync("python", ["--version"], {
      timeout: 5000,
    });
    const match = /Python (\d+)\.(\d+)/.exec(stdout);
    if (!match) {
      return { name: "python", status: "warn", detail: `unexpected output: ${stdout}` };
    }
    const major = Number(match[1]);
    if (major >= 3) {
      return { name: "python", status: "ok", detail: stdout.trim() };
    }
    return { name: "python", status: "fail", detail: `${stdout.trim()} below 3.13` };
  } catch (error) {
    return {
      name: "python",
      status: "fail",
      detail: `python not resolvable: ${String(error)}`,
    };
  }
}

async function checkDocker(): Promise<DoctorCheck> {
  try {
    const { stdout } = await execFileAsync(
      "docker",
      ["info", "--format", "{{.ServerVersion}}"],
      { timeout: 8000 },
    );
    return {
      name: "docker",
      status: "ok",
      detail: `Docker server ${stdout.trim()}`,
    };
  } catch {
    return {
      name: "docker",
      status: "warn",
      detail:
        "Docker is not available; runner-dependent commands will report capability_missing",
    };
  }
}

function checkBrowserInstall(): DoctorCheck {
  const localAppData = process.env.LOCALAPPDATA ?? "";
  const browserRoot = path.join(localAppData, "ms-playwright");
  return {
    name: "browser",
    status: localAppData && browserRoot ? "ok" : "warn",
    detail: localAppData
      ? `Playwright browsers expected under ${browserRoot}`
      : "LOCALAPPDATA is not set",
  };
}

async function checkRuntimeDir(runtimeDir: string): Promise<DoctorCheck> {
  try {
    await mkdir(runtimeDir, { recursive: true });
    await access(runtimeDir, constants.R_OK | constants.W_OK);
    return { name: "runtime", status: "ok", detail: `writable: ${runtimeDir}` };
  } catch (error) {
    return {
      name: "runtime",
      status: "fail",
      detail: `runtime dir not writable: ${String(error)}`,
    };
  }
}

async function checkControlPlane(
  controlHealthUrl?: string,
): Promise<DoctorCheck> {
  if (!controlHealthUrl) {
    return { name: "control-plane", status: "skip", detail: "not running" };
  }
  try {
    const response = await fetch(controlHealthUrl, {
      signal: AbortSignal.timeout(3000),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    return { name: "control-plane", status: "ok", detail: controlHealthUrl };
  } catch {
    return {
      name: "control-plane",
      status: "warn",
      detail: `health endpoint unreachable: ${controlHealthUrl}`,
    };
  }
}

async function checkEnvironment(runtimeDir: string): Promise<DoctorCheck> {
  try {
    const pathValue = path.join(runtimeDir, "tool-environment.json");
    const payload = JSON.parse(readFileSync(pathValue, "utf8")) as {
      digest?: string;
    };
    return {
      name: "tool-environment",
      status: "ok",
      detail: `digest ${payload.digest ?? "unknown"}`,
    };
  } catch {
    return {
      name: "tool-environment",
      status: "warn",
      detail: "no tool-environment.json; run veridix up --check/build",
    };
  }
}

async function checkConnectors(
  controlHealthUrl?: string,
): Promise<DoctorCheck> {
  if (!controlHealthUrl) {
    return {
      name: "connectors",
      status: "skip",
      detail: "control plane not configured",
    };
  }
  const base = controlHealthUrl.replace(/\/healthz$/, "");
  try {
    const response = await fetch(`${base}/api/v1/diagnostics`, {
      signal: AbortSignal.timeout(3000),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = (await response.json()) as {
      connectors?: Record<string, { status?: string }>;
    };
    const connectors = payload.connectors ?? {};
    const statuses = Object.entries(connectors).map(
      ([name, value]) => `${name}=${value?.status ?? "unknown"}`,
    );
    const unhealthy = Object.values(connectors).filter(
      (value) => value?.status === "unreachable",
    );
    return {
      name: "connectors",
      status: unhealthy.length ? "warn" : "ok",
      detail: statuses.join(" ") || "none configured",
    };
  } catch {
    return {
      name: "connectors",
      status: "warn",
      detail: "diagnostics endpoint unreachable",
    };
  }
}
