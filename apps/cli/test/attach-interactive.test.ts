import { PassThrough } from "node:stream";
import { describe, expect, it, vi } from "vitest";
import { attachInteractive } from "../src/attach-interactive.js";

function fakeClient() {
  return {
    getEvents: vi.fn(async () => []),
    getRun: vi.fn(async () => ({ status: "running" })),
    runCommand: vi.fn(async () => ({ status: "running" })),
    sendMessage: vi.fn(async () => ({ status: "running" })),
  };
}

describe("attach interactive", () => {
  it("handles pause, message and quit commands", async () => {
    const client = fakeClient();
    const input = new PassThrough();
    const output = new PassThrough();
    const chunks: string[] = [];
    output.on("data", (chunk: Buffer) => chunks.push(chunk.toString()));

    const pending = attachInteractive(
      client as never,
      "run_1",
      { input, output, pollInterval: 5 },
    );
    setTimeout(() => {
      input.write("pause\nmessage follow up\nquit\n");
    }, 20);
    const result = await pending;

    expect(client.runCommand).toHaveBeenCalledWith(
      "run_1",
      "pause",
      expect.any(String),
    );
    expect(client.sendMessage).toHaveBeenCalledWith(
      "run_1",
      "follow up",
      expect.any(String),
      "cli-operator",
    );
    expect(result.quit).toBe(true);
    expect(chunks.join("")).toContain("paused");
    expect(chunks.join("")).toContain("message sent");
  });

  it("prints help and handles unknown commands", async () => {
    const client = fakeClient();
    const input = new PassThrough();
    const output = new PassThrough();
    const chunks: string[] = [];
    output.on("data", (chunk: Buffer) => chunks.push(chunk.toString()));

    const pending = attachInteractive(
      client as never,
      "run_1",
      { input, output, pollInterval: 5 },
    );
    setTimeout(() => {
      input.write("help\nbogus\nquit\n");
    }, 20);
    await pending;

    const text = chunks.join("");
    expect(text).toContain("commands:");
    expect(text).toContain("unknown command: bogus");
  });
});
