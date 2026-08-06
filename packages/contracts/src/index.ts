export * from "./types.js";

export const SCHEMAS = {
  config: "schema/veridix-config.schema.json",
  inferenceProfile: "schema/inference-profile.schema.json",
  profile: "schema/profile.schema.json",
} as const;
