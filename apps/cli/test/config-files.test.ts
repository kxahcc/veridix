import { existsSync, mkdtempSync, rmSync, readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { initConfig, projectConfigPath } from "../src/config-files.js";

const tempDirs: string[] = [];

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

describe("init", () => {
  it("creates config, runtime directories, and empty SecretRefs", () => {
    const tmp = mkdtempSync(path.join(os.tmpdir(), "veridix-init-"));
    tempDirs.push(tmp);

    const created = initConfig(tmp);

    expect(existsSync(projectConfigPath(tmp))).toBe(true);
    expect(created).toContain(path.join(tmp, "runtime", "secrets"));
    expect(created).toContain(path.join(tmp, "runtime", "state"));
    const refs = JSON.parse(
      readFileSync(path.join(tmp, "runtime", "secrets", "refs.json"), "utf8"),
    );
    expect(refs).toEqual({});
  });
});
