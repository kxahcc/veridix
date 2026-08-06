import { describe, expect, it } from "vitest";
import { runSelfTest } from "../src/self-test.js";

describe("self-test tiers", () => {
  it("passes contract, evidence, and report fixtures locally", async () => {
    const result = await runSelfTest({
      runtimeDir: process.cwd(),
      controlHealthUrl: "http://127.0.0.1:1/healthz",
    });

    expect(result.tiers.contract.status).toBe("ok");
    expect(result.tiers.evidence.status).toBe("ok");
    expect(result.tiers.report.status).toBe("ok");
    expect(result.tiers.continuity.status).toBe("skip");
    expect(result.tiers["local-target"].status).toBe("skip");
  });

  it("starts the local lab target when enabled", async () => {
    process.env.VERIDIX_SELF_TEST_LOCAL_TARGET = "1";
    try {
      const result = await runSelfTest({
        runtimeDir: process.cwd(),
        controlHealthUrl: "http://127.0.0.1:1/healthz",
      });
      expect(result.tiers["local-target"].status).toBe("ok");
    } finally {
      delete process.env.VERIDIX_SELF_TEST_LOCAL_TARGET;
    }
  }, 30_000);
});
