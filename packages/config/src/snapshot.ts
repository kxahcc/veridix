import { createHash } from "node:crypto";
import type { VeridixConfig } from "@veridix/contracts";

export interface ConfigSnapshot {
  config: VeridixConfig;
  hash: string;
  createdAt: string;
}

export function createConfigSnapshot(config: VeridixConfig): ConfigSnapshot {
  const canonical = JSON.stringify(config);
  return {
    config,
    hash: createHash("sha256").update(canonical).digest("hex"),
    createdAt: new Date().toISOString(),
  };
}
