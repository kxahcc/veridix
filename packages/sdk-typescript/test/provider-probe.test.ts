import { createServer } from "node:http";
import { afterEach, describe, expect, it } from "vitest";
import type { InferenceProfile } from "@veridix/contracts";
import { probeProvider } from "../src/provider-probe.js";

const servers: Array<ReturnType<typeof createServer>> = [];

afterEach(() => {
  for (const server of servers.splice(0)) {
    server.close();
  }
});

function startMockProvider() {
  const server = createServer((req, res) => {
    if (req.method === "GET" && req.url?.endsWith("/models")) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(
        JSON.stringify({
          object: "list",
          data: [
            { id: "fixture-model", object: "model" },
            { id: "fixture-embed", object: "model" },
          ],
        }),
      );
      return;
    }
    if (req.method === "POST" && req.url?.endsWith("/embeddings")) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(
        JSON.stringify({
          data: [{ object: "embedding", embedding: [0.1, 0.2, 0.3], index: 0 }],
        }),
      );
      return;
    }
    if (req.method === "POST" && req.url?.endsWith("/rerank")) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(
        JSON.stringify({
          results: [
            { index: 0, score: 0.9 },
            { index: 1, score: 0.1 },
          ],
        }),
      );
      return;
    }
    res.writeHead(404);
    res.end();
  });
  servers.push(server);
  return new Promise<string>((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (address && typeof address === "object") {
        resolve(`http://127.0.0.1:${address.port}`);
      }
    });
  });
}

function profile(endpoint: string): InferenceProfile {
  return {
    providerId: "fixture",
    model: "fixture-model",
    endpoint,
    dataPolicy: "local",
    timeoutSeconds: 10,
  };
}

describe("provider probe", () => {
  it("detects models, embedding dimensions, and rerank batch support", async () => {
    const endpoint = await startMockProvider();

    const inference = await probeProvider(profile(endpoint), "inference");
    const embedding = await probeProvider(profile(endpoint), "embedding");
    const rerank = await probeProvider(profile(endpoint), "rerank");

    expect(inference.status).toBe("ok");
    expect(inference.capabilities?.models).toEqual([
      "fixture-embed",
      "fixture-model",
    ]);
    expect(embedding.status).toBe("ok");
    expect(embedding.capabilities?.dimensions).toBe(3);
    expect(rerank.status).toBe("ok");
    expect(rerank.capabilities?.supportsBatch).toBe(true);
  });

  it("classifies unreachable provider as unavailable", async () => {
    const result = await probeProvider(profile("http://127.0.0.1:1"), "inference");

    expect(result.status).toBe("unavailable");
    expect(result.detail).toContain("inference_unavailable");
  });
});
