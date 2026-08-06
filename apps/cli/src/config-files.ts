import { randomUUID } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  DEFAULT_CONFIG,
  ConfigMergeResult,
  mergeConfig,
} from "@veridix/config";
import type { VeridixConfig, ProfileName } from "@veridix/contracts";

export const RUNTIME_DIRS = [
  "logs",
  "secrets",
  "artifacts",
  "checkpoints",
  "state",
] as const;

export function projectConfigPath(rootDir: string): string {
  return path.join(rootDir, "veridix.config.json");
}

export function userConfigPath(): string {
  return path.join(os.homedir(), ".veridix", "config.json");
}

export function loadConfig(
  rootDir: string,
  cliOverrides?: Record<string, unknown>,
): ConfigMergeResult {
  const layers: Array<{ layer: "defaults" | "profile" | "project" | "user" | "cli"; value: unknown }> = [
    { layer: "defaults", value: DEFAULT_CONFIG },
  ];

  const projectPath = projectConfigPath(rootDir);
  let profileName: ProfileName = "desktop";
  if (existsSync(projectPath)) {
    const projectConfig = JSON.parse(readFileSync(projectPath, "utf8"));
    layers.push({ layer: "project", value: projectConfig });
    if (projectConfig.profile) {
      profileName = projectConfig.profile as ProfileName;
    }
  }

  const profileDefaults = PROFILE_DEFAULTS[profileName];
  if (profileDefaults) {
    layers.push({ layer: "profile", value: profileDefaults });
  }

  const userPath = userConfigPath();
  if (existsSync(userPath)) {
    layers.push({ layer: "user", value: JSON.parse(readFileSync(userPath, "utf8")) });
  }
  if (cliOverrides) {
    layers.push({ layer: "cli", value: cliOverrides });
  }
  return mergeConfig(layers, DEFAULT_CONFIG);
}

export function initConfig(rootDir: string): string[] {
  const configPath = projectConfigPath(rootDir);
  const created: string[] = [];
  if (!existsSync(configPath)) {
    const config: VeridixConfig = {
      ...structuredClone(DEFAULT_CONFIG),
      project: {
        id: `project_${randomUUID().slice(0, 8)}`,
        name: path.basename(rootDir),
      },
    };
    writeFileSync(configPath, `${JSON.stringify(config, null, 2)}\n`);
    created.push(configPath);
  }

  const runtimeDir = path.join(rootDir, DEFAULT_CONFIG.runtime?.dir ?? "runtime");
  for (const dir of RUNTIME_DIRS) {
    const target = path.join(runtimeDir, dir);
    mkdirSync(target, { recursive: true });
    created.push(target);
  }

  const secretsRefPath = path.join(runtimeDir, "secrets", "refs.json");
  if (!existsSync(secretsRefPath)) {
    writeFileSync(secretsRefPath, "{}\n");
    created.push(secretsRefPath);
  }
  return created;
}

export function setProfile(rootDir: string, profile: ProfileName): VeridixConfig {
  if (!(profile in PROFILE_DEFAULTS)) {
    throw new Error(`unknown profile ${profile}`);
  }
  const configPath = projectConfigPath(rootDir);
  const current = existsSync(configPath)
    ? (JSON.parse(readFileSync(configPath, "utf8")) as VeridixConfig)
    : structuredClone(DEFAULT_CONFIG);
  const next: VeridixConfig = { ...current, profile };
  writeFileSync(configPath, `${JSON.stringify(next, null, 2)}\n`);
  return next;
}

export const PROFILE_DEFAULTS: Record<ProfileName, Partial<VeridixConfig> | undefined> = {
  desktop: undefined,
  lab: {
    security: {
      sandbox: {
        maxMemoryMb: 2048,
      },
    },
  },
  server: {
    security: {
      sandbox: {
        network: "none",
      },
    },
  },
  airgap: {
    security: {
      dataPolicy: {
        allowedLabels: ["public", "project"],
      },
    },
  },
};
