import { writeFile } from "node:fs/promises";
import path from "node:path";

export function reportFileName(runId: string): string {
  return `report-${runId}.zip`;
}

export async function downloadReport(
  runId: string,
  baseUrl: string,
  outDir: string,
): Promise<string> {
  let lastError: unknown;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const response = await fetch(
        `${baseUrl}/api/v1/runs/${runId}/report-bundle`,
      );
      if (!response.ok) {
        throw new Error(
          `report download failed: HTTP ${response.status}`,
        );
      }
      const data = Buffer.from(await response.arrayBuffer());
      const outPath = path.join(outDir, reportFileName(runId));
      await writeFile(outPath, data);
      return outPath;
    } catch (error) {
      lastError = error;
      await new Promise((resolve) =>
        setTimeout(resolve, 200 * (attempt + 1)),
      );
    }
  }
  throw lastError;
}
