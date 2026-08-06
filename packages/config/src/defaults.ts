import type { VeridixConfig } from "@veridix/contracts";

export const DEFAULT_CONFIG: VeridixConfig = {
  version: 1,
  project: {
    id: "local-dev",
    name: "Local Development",
  },
  profile: "desktop",
  runtime: {
    dir: "runtime",
  },
  security: {
    targetScope: {
      allowed: [],
      excluded: [],
    },
    sandbox: {
      maxMemoryMb: 1024,
      maxCpus: 2,
      network: "proxy",
    },
    dataPolicy: {
      allowedLabels: ["public", "project"],
    },
  },
};
