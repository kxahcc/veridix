import { describe, expect, it } from "vitest";
import type { VeridixConfig } from "@veridix/contracts";
import {
  DEFAULT_CONFIG,
  createConfigSnapshot,
  explainKey,
  mergeConfig,
} from "../src/index.js";

function layer(value: Partial<VeridixConfig>): VeridixConfig {
  return {
    ...structuredClone(DEFAULT_CONFIG),
    ...value,
  };
}

describe("config merge", () => {
  it("later non-security layers override earlier layers", () => {
    const project = layer({
      project: { id: "project_x", name: "X" },
    });

    const result = mergeConfig(
      [
        { layer: "project", value: project },
        { layer: "cli", value: { project: { id: "project_cli", name: "CLI" } } },
      ],
      DEFAULT_CONFIG,
    );

    expect(result.config.project.id).toBe("project_cli");
    expect(result.sources["project.id"]).toBe("cli");
  });

  it("allowed target scope is clipped to intersection", () => {
    const profile = layer({
      security: {
        targetScope: { allowed: ["https://a.test", "https://b.test"], excluded: [] },
      },
    });
    const project = layer({
      security: {
        targetScope: { allowed: ["https://b.test", "https://c.test"], excluded: [] },
      },
    });

    const result = mergeConfig(
      [
        { layer: "profile", value: profile },
        { layer: "project", value: project },
      ],
      DEFAULT_CONFIG,
    );

    expect(result.config.security?.targetScope?.allowed).toEqual([
      "https://b.test",
    ]);
  });

  it("sandbox memory limit cannot be widened by a higher layer", () => {
    const profile = layer({ security: { sandbox: { maxMemoryMb: 1024 } } });
    const project = layer({ security: { sandbox: { maxMemoryMb: 4096 } } });

    const result = mergeConfig(
      [
        { layer: "profile", value: profile },
        { layer: "project", value: project },
      ],
      DEFAULT_CONFIG,
    );

    expect(result.config.security?.sandbox?.maxMemoryMb).toBe(1024);
    expect(result.clipped).toContainEqual(
      expect.objectContaining({
        key: "security.sandbox.maxMemoryMb",
        reason: "higher layer cannot widen security.sandbox.maxMemoryMb",
      }),
    );
    expect(result.sources["security.sandbox.maxMemoryMb"]).toBe("profile");
  });

  it("explain returns value, source, and clip reason", () => {
    const profile = layer({ security: { sandbox: { network: "proxy" } } });
    const result = mergeConfig(
      [{ layer: "profile", value: profile }],
      DEFAULT_CONFIG,
    );

    const explained = explainKey(result, "security.sandbox.network");

    expect(explained.value).toBe("proxy");
    expect(explained.source).toBe("profile");
    expect(explained.clippedReason).toBeNull();
  });

  it("config snapshot hash is deterministic", () => {
    const first = createConfigSnapshot(DEFAULT_CONFIG);
    const second = createConfigSnapshot(DEFAULT_CONFIG);

    expect(first.hash).toBe(second.hash);
    expect(first.hash).toMatch(/^[0-9a-f]{64}$/);
  });
});
