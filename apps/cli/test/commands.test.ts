import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { promisify } from "node:util";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { findRepoRoot } from "../src/paths.js";

const execFileAsync = promisify(execFile);

async function runCli(
  args: string[],
): Promise<{ code: number; stdout: string; stderr: string }> {
  const root = findRepoRoot();
  const cli = path.join(root, "apps", "cli", "dist", "index.js");
  try {
    const result = await execFileAsync("node", [cli, ...args], {
      env: {
        ...process.env,
        VERIDIX_CONTROL_URL: "http://127.0.0.1:1",
      },
      timeout: 30_000,
    });
    return { code: 0, stdout: result.stdout, stderr: result.stderr };
  } catch (error) {
    const err = error as { code?: number; stdout?: string; stderr?: string };
    return {
      code: err.code ?? 1,
      stdout: err.stdout ?? "",
      stderr: err.stderr ?? "",
    };
  }
}

describe("CLI lifecycle commands", () => {
  it("bench --dry-run returns a plan", async () => {
    const result = await runCli(["bench", "--dry-run"]);

    expect(result.code).toBe(0);
    expect(JSON.parse(result.stdout).dry_run).toBe(true);
  });

  it("bench --suite role --dry-run plans the role comparison", async () => {
    const result = await runCli(["bench", "--suite", "role", "--dry-run"]);

    expect(result.code).toBe(0);
    expect(JSON.parse(result.stdout).plan[0].suite).toBe("role");
  });

  it("upgrade --check prints pinned versions", async () => {
    const result = await runCli(["upgrade", "--check"]);

    expect(result.code).toBe(0);
    expect(JSON.parse(result.stdout).runtime.node).toBeTruthy();
  });

  it("pack list prints the Tool Pack catalog", async () => {
    const result = await runCli(["pack", "list"]);

    expect(result.code).toBe(0);
    const packs = result.stdout
      .trim()
      .split(/\r?\n/)
      .map((line) => JSON.parse(line));
    expect(packs.map((pack: { name: string }) => pack.name)).toContain("web");
  });

  it("pack export --dry-run returns an export plan", async () => {
    const result = await runCli([
      "pack",
      "export",
      "--out",
      "dist-product/tools.tar.gz",
      "--dry-run",
    ]);

    expect(result.code).toBe(0);
    expect(JSON.parse(result.stdout).dry_run).toBe(true);
  });

  it("pack airgap --dry-run returns an assembly plan", async () => {
    const result = await runCli([
      "pack",
      "airgap",
      "--out",
      "dist-product/airgap.zip",
      "--desktop-zip",
      "dist-product/desktop.zip",
      "--tools-tar",
      "dist-product/tools.tar.gz",
      "--key",
      "abc",
      "--dry-run",
    ]);

    expect(result.code).toBe(0);
    expect(JSON.parse(result.stdout).action).toBe("airgap");
  });

  it("up --check prints the tool pack preflight plan", async () => {
    const result = await runCli([
      "up",
      "--check",
      "--fetch",
      "--registry",
      "registry.example.test",
    ]);

    expect(result.code).toBe(0);
    const payload = JSON.parse(result.stdout);
    expect(payload.tool_packs).toHaveLength(9);
    expect(payload.tool_packs[0].fetch).toBe(true);
    expect(payload.tool_packs[0].registry).toBe("registry.example.test");
  });

  it("mcp help lists management commands", async () => {
    const result = await runCli(["mcp", "--help"]);

    expect(result.code).toBe(0);
    expect(result.stdout).toContain("register");
    expect(result.stdout).toContain("delete");
    expect(result.stdout).toContain("test");
  });

  it("mission help documents spec file option", async () => {
    const result = await runCli(["mission", "--help"]);

    expect(result.code).toBe(0);
    expect(result.stdout).toContain("--spec-file");
  });

  it("mission help documents loop profile overrides", async () => {
    const result = await runCli(["mission", "--help"]);

    expect(result.code).toBe(0);
    expect(result.stdout).toContain("--loop-profiles");
  });

  it("mission help documents loop preset selection", async () => {
    const result = await runCli(["mission", "--help"]);

    expect(result.code).toBe(0);
    expect(result.stdout).toContain("--loop-preset");
  });

  it("loop-presets help describes reusable presets", async () => {
    const result = await runCli(["loop-presets", "--help"]);

    expect(result.code).toBe(0);
    expect(result.stdout).toContain("Loop Profile presets");
  });

  it("provider register requires endpoint and model", async () => {
    const result = await runCli(["provider", "register", "deepseek"]);

    expect(result.code).not.toBe(0);
    expect(result.stderr).toContain("--endpoint");
  });

  it("skills-register help lists required name option", async () => {
    const result = await runCli(["skills-register", "--help"]);

    expect(result.code).toBe(0);
    expect(result.stdout).toContain("--name");
  });

  it("loop-profiles help describes declarative loop profiles", async () => {
    const result = await runCli(["loop-profiles", "--help"]);

    expect(result.code).toBe(0);
    expect(result.stdout).toContain("declarative loop profiles");
  });

  it("knowledge help lists delete command", async () => {
    const result = await runCli(["knowledge", "--help"]);

    expect(result.code).toBe(0);
    expect(result.stdout).toContain("delete");
  });

  it("acceptance help describes the unified summary", async () => {
    const result = await runCli(["acceptance", "--help"]);

    expect(result.code).toBe(0);
    expect(result.stdout).toContain("unified acceptance");
  });

  it("memory help lists management subcommands", async () => {
    const result = await runCli(["memory", "--help"]);

    expect(result.code).toBe(0);
    expect(result.stdout).toContain("list");
    expect(result.stdout).toContain("record");
    expect(result.stdout).toContain("fix");
    expect(result.stdout).toContain("forget");
    expect(result.stdout).toContain("clear");
  });

  it("memory fix help documents required options", async () => {
    const result = await runCli(["memory", "fix", "--help"]);

    expect(result.code).toBe(0);
    expect(result.stdout).toContain("--subject");
    expect(result.stdout).toContain("--predicate");
    expect(result.stdout).toContain("--value");
  });

  it("memory record help documents required options", async () => {
    const result = await runCli(["memory", "record", "--help"]);

    expect(result.code).toBe(0);
    expect(result.stdout).toContain("--subject");
    expect(result.stdout).toContain("--predicate");
    expect(result.stdout).toContain("--value");
  });

  it("assets help lists project asset subcommands", async () => {
    const result = await runCli(["assets", "--help"]);

    expect(result.code).toBe(0);
    expect(result.stdout).toContain("list");
    expect(result.stdout).toContain("import");
    expect(result.stdout).toContain("export");
  });

  it("assets list help documents project filtering", async () => {
    const result = await runCli(["assets", "list", "--help"]);

    expect(result.code).toBe(0);
    expect(result.stdout).toContain("--project-id");
  });

  it("assets add help documents required fields", async () => {
    const result = await runCli(["assets", "add", "--help"]);

    expect(result.code).toBe(0);
    expect(result.stdout).toContain("--project-id");
    expect(result.stdout).toContain("--value");
  });

  it("report help documents format selection", async () => {
    const result = await runCli(["report", "--help"]);

    expect(result.code).toBe(0);
    expect(result.stdout).toContain("--format");
    expect(result.stdout).toContain("markdown");
    expect(result.stdout).toContain("html");
  });

  it("vulns help documents list and update", async () => {
    const result = await runCli(["vulns", "--help"]);

    expect(result.code).toBe(0);
    expect(result.stdout).toContain("list");
    expect(result.stdout).toContain("update");
  });

  it("risk help documents project filtering", async () => {
    const result = await runCli(["risk", "--help"]);

    expect(result.code).toBe(0);
    expect(result.stdout).toContain("--project-id");
  });

  it("trace fails cleanly when control plane is unavailable", async () => {
    const result = await runCli(["trace", "run_missing"]);

    expect(result.code).not.toBe(0);
    expect(result.stderr.length).toBeGreaterThan(0);
  });

  it("project list fails cleanly when control plane is unavailable", async () => {
    const result = await runCli(["project", "list"]);

    expect(result.code).not.toBe(0);
    expect(result.stderr.length).toBeGreaterThan(0);
  });

  it("provider list fails cleanly when control plane is unavailable", async () => {
    const result = await runCli(["provider", "list"]);

    expect(result.code).not.toBe(0);
    expect(result.stderr.length).toBeGreaterThan(0);
  });

  it("knowledge list returns empty for a fresh db", async () => {
    const dir = await mkdtemp(path.join(os.tmpdir(), "veridix-knowledge-"));
    try {
      const result = await runCli([
        "knowledge",
        "list",
        "--db",
        path.join(dir, "knowledge.db"),
      ]);

      expect(result.code).toBe(0);
      expect(JSON.parse(result.stdout).chunks).toEqual([]);
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });

  it("doctor --bundle writes a support bundle", async () => {
    const dir = await mkdtemp(path.join(os.tmpdir(), "veridix-doctor-"));
    try {
      const bundle = path.join(dir, "doctor.json");
      const result = await runCli(["doctor", "--bundle", bundle]);

      expect(result.code).toBe(0);
      const payload = JSON.parse(await readFile(bundle, "utf8"));
      expect(payload.checks.length).toBeGreaterThan(0);
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });
});
