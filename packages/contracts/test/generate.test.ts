import { readFileSync } from "node:fs";
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const contractsRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);

describe("schema type generation", () => {
  it("generates TypeScript interfaces from JSON Schemas", () => {
    execSync("python scripts/generate_types.py", {
      cwd: contractsRoot,
      stdio: "pipe",
    });
    const generated = readFileSync(
      path.join(contractsRoot, "src/generated/types.ts"),
      "utf8",
    );

    expect(generated).toContain("export interface AgentEventEnvelope {");
    expect(generated).toContain("schemaVersion: 1;");
    expect(generated).toContain("payload: Record<string, unknown>;");
    expect(generated).toContain("export interface VeridixConfig {");
    expect(generated).toContain("requestOptions?: {");
    expect(generated).toContain('thinkingMode?: "enabled" | "disabled";');
  });
});
