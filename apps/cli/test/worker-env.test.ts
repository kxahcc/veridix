import { afterEach, describe, expect, it } from "vitest";
import type { VeridixConfig } from "@veridix/contracts";
import {
  applyWorkerEnv,
  workerEnvFromConfig,
} from "../src/worker-env.js";

const ENV_KEYS = [
  "VERIDIX_PROVIDER_ENDPOINT",
  "VERIDIX_PROVIDER_MODEL",
  "VERIDIX_PROVIDER_API_KEY_REF",
  "VERIDIX_WORKER_AUTOPILOT",
  "VERIDIX_WORKER_STREAMING",
] as const;

afterEach(() => {
  for (const key of ENV_KEYS) {
    delete process.env[key];
  }
});

function baseConfig(): VeridixConfig {
  return {
    version: 1,
    project: { id: "project_test", name: "test" },
    profile: "desktop",
  };
}

describe("worker env from config", () => {
  it("maps provider inference to autopilot env", () => {
    const config: VeridixConfig = {
      ...baseConfig(),
      provider: {
        inference: {
          providerId: "deepseek",
          model: "deepseek-v4-flash",
          endpoint: "https://api.deepseek.com",
          apiKeyRef: "env:DEEPSEEK_API_KEY",
          dataPolicy: "remote",
          timeoutSeconds: 30,
          capabilities: { streaming: true },
        },
      },
    };

    const env = workerEnvFromConfig(config);

    expect(env.autopilot).toBe(true);
    expect(env.streaming).toBe(true);
    expect(env.providerEndpoint).toBe("https://api.deepseek.com");
    expect(env.apiKeyRef).toBe("env:DEEPSEEK_API_KEY");
  });

  it("applies configured provider to process env", () => {
    const config: VeridixConfig = {
      ...baseConfig(),
      provider: {
        inference: {
          providerId: "deepseek",
          model: "deepseek-v4-flash",
          endpoint: "https://api.deepseek.com",
          dataPolicy: "remote",
          timeoutSeconds: 30,
        },
      },
    };

    applyWorkerEnv(config);

    expect(process.env.VERIDIX_PROVIDER_ENDPOINT).toBe("https://api.deepseek.com");
    expect(process.env.VERIDIX_PROVIDER_MODEL).toBe("deepseek-v4-flash");
    expect(process.env.VERIDIX_WORKER_AUTOPILOT).toBe("1");
    expect(process.env.VERIDIX_WORKER_STREAMING).toBe("0");
  });

  it("leaves env untouched without a provider", () => {
    applyWorkerEnv(baseConfig());

    for (const key of ENV_KEYS) {
      expect(process.env[key]).toBeUndefined();
    }
  });
});
