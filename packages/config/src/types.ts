import type { VeridixConfig } from "@veridix/contracts";

export type ConfigLayer =
  | "defaults"
  | "profile"
  | "project"
  | "user"
  | "mission"
  | "cli";

export interface ClippedField {
  key: string;
  layer: ConfigLayer;
  reason: string;
}

export interface ConfigMergeResult {
  config: VeridixConfig;
  sources: Record<string, ConfigLayer>;
  clipped: ClippedField[];
}

export interface ExplainedValue {
  key: string;
  value: unknown;
  source: ConfigLayer | null;
  clippedReason: string | null;
}
