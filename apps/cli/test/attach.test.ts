import { createServer } from "node:http";
import { afterEach, describe, expect, it } from "vitest";
import { ControlClient } from "@veridix/sdk-typescript";
import { attachOnce } from "../src/attach.js";

const servers: ReturnType<typeof createServer>[] = [];

afterEach(() => {
  for (const server of servers.splice(0)) {
    server.close();
  }
});

describe("attachOnce", () => {
  it("advances the cursor and reports terminal runs", async () => {
    const server = createServer((req, res) => {
      res.writeHead(200, { "content-type": "application/json" });
      if (req.url?.startsWith("/api/v1/runs/run_1/events")) {
        res.end(
          JSON.stringify([
            {
              event_id: "e_1",
              event_type: "model.turn.started",
              sequence: 3,
            },
          ]),
        );
        return;
      }
      res.end(
        JSON.stringify({
          run_id: "run_1",
          status: "succeeded",
          event_count: 3,
          observations: [],
          stop_reason: "model.finish",
          created_at: "2026-08-02T00:00:00Z",
          mission_id: "mission_1",
        }),
      );
    });
    await new Promise<void>((resolve) =>
      server.listen(0, "127.0.0.1", resolve),
    );
    servers.push(server);
    const address = server.address();
    const client = new ControlClient(`http://127.0.0.1:${address.port}`);

    const result = await attachOnce(client, "run_1", 0);

    expect(result.cursor).toBe(3);
    expect(result.terminal).toBe(true);
    expect(result.events).toHaveLength(1);
    expect(result.run.status).toBe("succeeded");
  });
});
