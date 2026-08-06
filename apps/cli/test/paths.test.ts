import { mkdirSync, writeFileSync } from "node:fs";
import { mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { findPython } from "../src/paths.js";

const tempDirs: string[] = [];

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

describe("findPython", () => {
  it("prefers the bundled python-runtime when present", () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "veridix-python-"));
    tempDirs.push(root);
    const runtimeDir = path.join(root, "python-runtime");
    mkdirSync(runtimeDir, { recursive: true });
    const bundled = path.join(
      runtimeDir,
      process.platform === "win32" ? "python.exe" : "bin/python",
    );
    if (process.platform !== "win32") {
      mkdirSync(path.join(runtimeDir, "bin"), { recursive: true });
    }
    writeFileSync(bundled, "");

    expect(findPython(root)).toBe(bundled);
  });

  it("prefers the project venv python when present", () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "veridix-python-"));
    tempDirs.push(root);
    const venvDir =
      process.platform === "win32"
        ? path.join(root, ".venv", "Scripts")
        : path.join(root, ".venv", "bin");
    mkdirSync(venvDir, { recursive: true });
    const python = path.join(
      venvDir,
      process.platform === "win32" ? "python.exe" : "python",
    );
    writeFileSync(python, "");

    expect(findPython(root)).toBe(python);
  });

  it("falls back to python without a venv", () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "veridix-python-"));
    tempDirs.push(root);

    expect(findPython(root)).toBe("python");
  });
});
