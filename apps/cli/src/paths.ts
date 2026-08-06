import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

export function findRepoRoot(start = process.cwd()): string {
  let current = path.resolve(start);
  while (true) {
    const packagePath = path.join(current, "package.json");
    if (existsSync(packagePath)) {
      try {
        const manifest = JSON.parse(readFileSync(packagePath, "utf8"));
        if (Array.isArray(manifest.workspaces)) {
          return current;
        }
      } catch {
        // keep walking up
      }
    }
    const parent = path.dirname(current);
    if (parent === current) {
      throw new Error("not inside the veridix repository");
    }
    current = parent;
  }
}

export function findPython(rootDir: string): string {
  const bundledPython =
    process.platform === "win32"
      ? path.join(rootDir, "python-runtime", "python.exe")
      : path.join(rootDir, "python-runtime", "bin", "python");
  if (existsSync(bundledPython)) {
    return bundledPython;
  }
  const venvPython =
    process.platform === "win32"
      ? path.join(rootDir, ".venv", "Scripts", "python.exe")
      : path.join(rootDir, ".venv", "bin", "python");
  return existsSync(venvPython) ? venvPython : "python";
}
