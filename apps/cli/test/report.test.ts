import { createServer } from "node:http";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { downloadReport, reportFileName } from "../src/report.js";

const tempDirs: string[] = [];

afterEach(async () => {
  for (const dir of tempDirs.splice(0)) {
    await rm(dir, { recursive: true, force: true });
  }
});

describe("downloadReport", () => {
  it("writes the report bundle to the output directory", async () => {
    const outDir = await mkdtemp(path.join(os.tmpdir(), "veridix-report-"));
    tempDirs.push(outDir);
    const server = createServer((_req, res) => {
      res.writeHead(200, { "content-type": "application/zip" });
      res.end(Buffer.from("report-bytes"));
    });
    await new Promise<void>((resolve) =>
      server.listen(0, "127.0.0.1", resolve),
    );
    const address = server.address();

    const outPath = await downloadReport(
      "run_1",
      `http://127.0.0.1:${address.port}`,
      outDir,
    );
    server.close();

    expect(path.basename(outPath)).toBe(reportFileName("run_1"));
    expect(await readFile(outPath, "utf8")).toBe("report-bytes");
  });
});
