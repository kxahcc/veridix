import { execFile } from "node:child_process";
import { promisify } from "node:util";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { findRepoRoot } from "../src/paths.js";

const execFileAsync = promisify(execFile);

async function runCli(args: string[]): Promise<{ code: number; stderr: string }> {
  const root = findRepoRoot();
  const cli = path.join(root, "apps", "cli", "dist", "index.js");
  try {
    await execFileAsync("node", [cli, ...args], {
      env: {
        ...process.env,
        VERIDIX_CONTROL_URL: "http://127.0.0.1:1",
      },
      timeout: 10_000,
    });
    return { code: 0, stderr: "" };
  } catch (error) {
    const err = error as { code?: number; stderr?: string };
    return { code: err.code ?? 1, stderr: err.stderr ?? "" };
  }
}

describe("CLI non-stuck behavior", () => {
  it("run status fails cleanly when control plane is unavailable", async () => {
    const result = await runCli(["run", "status", "run_missing"]);

    expect(result.code).not.toBe(0);
    expect(result.stderr.length).toBeGreaterThan(0);
  });

  it("run attach --once fails cleanly when control plane is unavailable", async () => {
    const result = await runCli(["run", "attach", "run_missing", "--once"]);

    expect(result.code).not.toBe(0);
    expect(result.stderr.length).toBeGreaterThan(0);
  });
});
