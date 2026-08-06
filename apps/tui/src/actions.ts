import type { AgentEvent, ApprovalRequest } from "@veridix/sdk-typescript";
import { writeFile } from "node:fs/promises";
import path from "node:path";

export const SLASH_COMMANDS = [
  "/help",
  "/new",
  "/new-code",
  "/runs",
  "/providers",
  "/provider-add",
  "/provider-default",
  "/provider-test",
  "/skills",
  "/skill-add",
  "/skill-delete",
  "/mcp",
  "/mcp-add",
  "/mcp-test",
  "/nodes",
  "/dispatch",
  "/acceptance",
  "/tools",
  "/audit",
  "/health",
  "/loop-profiles",
  "/loop-presets",
  "/retrieval",
  "/retrieval-set",
  "/knowledge",
  "/knowledge-add",
  "/knowledge-delete",
  "/memory",
  "/memory-record",
  "/memory-fix",
  "/memory-clear",
  "/assets-list",
  "/asset-add",
  "/asset-update",
  "/asset-delete",
  "/report",
  "/sessions",
  "/vulns",
  "/risk",
  "/msg",
  "/filter",
  "/fork",
  "/takeover",
  "/exit",
] as const;

export function reportFileName(runId: string): string {
  return `report-${runId}.zip`;
}

export function completeSlashCommand(
  draft: string,
  commands: readonly string[] = SLASH_COMMANDS,
): { draft?: string; hint?: string } {
  const prefix = draft.replace(/^\//, "").trim().toLowerCase();
  if (!prefix) {
    return { hint: "输入前缀后按 Tab 补全" };
  }
  const matches = commands.filter((command) =>
    command.toLowerCase().startsWith(`/${prefix}`),
  );
  if (matches.length === 1) {
    return { draft: matches[0] };
  }
  if (matches.length > 1) {
    return { hint: `匹配 ${matches.length}: ${matches.join(" ")}` };
  }
  return { hint: "无匹配命令" };
}

export function nextHistoryIndex(
  current: number,
  direction: -1 | 1,
  length: number,
): number {
  if (length <= 0) {
    return -1;
  }
  if (direction === -1) {
    return current <= 0 ? length - 1 : current - 1;
  }
  if (current === -1) {
    return 0;
  }
  return current + 1 >= length ? -1 : current + 1;
}

export async function exportReportBundle(
  runId: string,
  baseUrl: string,
  outDir = process.cwd(),
): Promise<string> {
  let lastError: unknown;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const response = await fetch(
        `${baseUrl}/api/v1/runs/${runId}/report-bundle`,
      );
      if (!response.ok) {
        throw new Error(
          `report export failed: HTTP ${response.status}`,
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

export function firstPendingApproval(
  approvals: ApprovalRequest[],
): ApprovalRequest | undefined {
  return approvals.find((approval) => approval.state === "requested");
}

export interface DiagnosticsSummary {
  toolDigest: string;
  connectors: Record<string, string>;
}

export function formatDiagnosticsSummary(
  data: Record<string, unknown>,
): DiagnosticsSummary {
  const tool = data.tool_environment as
    | { digest?: string }
    | undefined;
  const connectors = data.connectors as
    | Record<string, { status?: string }>
    | undefined;
  return {
    toolDigest: tool?.digest ?? "",
    connectors: Object.fromEntries(
      Object.entries(connectors ?? {}).map(([name, value]) => [
        name,
        value?.status ?? "unknown",
      ]),
    ),
  };
}

export function formatGraphAscii(
  events: AgentEvent[],
  metrics: Record<string, unknown> | null,
): string[] {
  const nodes = events
    .filter((event) => event.event_type === "graph.node.completed")
    .map(
      (event) =>
        `node ${String(event.payload.node_id)}: ${String(
          event.payload.status,
        )}`,
    );
  const handoffs = events
    .filter((event) => event.event_type === "graph.handoff")
    .map(
      (event) =>
        `handoff ${String(event.payload.from_node)} -> ${String(
          event.payload.to_node,
        )} facts=${
          (event.payload.fact_refs as unknown[] | undefined)?.length ?? 0
        }`,
    );
  const metricLines = (
    (metrics?.metrics as
      | Array<Record<string, unknown>>
      | undefined) ?? []
  ).map(
    (metric) =>
      `graph metrics: handoffs=${String(metric.handoffs)} ` +
      `dead_letters=${String(metric.dead_letters)} ` +
      `duplicate_actions=${String(metric.duplicate_actions)} ` +
      `path_efficiency=${String(metric.path_efficiency)}`,
  );
  return [...nodes, ...handoffs, ...metricLines];
}

export function formatMemoryAscii(events: AgentEvent[]): string[] {
  const projections = events.filter(
    (event) => event.event_type === "context.projection",
  );
  if (projections.length === 0) {
    return ["no memory projection"];
  }
  const payload = projections[projections.length - 1].payload as {
    memory?: {
      snapshot?: {
        active: number;
        conflict: number;
        stale: number;
        total_facts: number;
      };
      facts?: Array<{
        fact_id: string;
        subject: string;
        predicate: string;
        value: string;
        status: string;
        expires_at?: string;
      }>;
    };
  };
  const memory = payload.memory;
  const lines: string[] = [];
  if (memory?.snapshot) {
    lines.push(
      `memory: active=${memory.snapshot.active} ` +
        `conflict=${memory.snapshot.conflict} ` +
        `stale=${memory.snapshot.stale} ` +
        `total=${memory.snapshot.total_facts}`,
    );
  }
  for (const fact of memory?.facts ?? []) {
    lines.push(
      `fact ${fact.fact_id} ${fact.subject} ` +
        `${fact.predicate}=${fact.value} status=${fact.status}` +
        (fact.expires_at ? ` expires=${fact.expires_at}` : ""),
    );
  }
  return lines;
}

export function formatMemoryApiAscii(
  payload: Record<string, unknown>,
): string[] {
  const snapshot = payload.snapshot as
    | {
        active?: number;
        conflict?: number;
        stale?: number;
        total_facts?: number;
      }
    | undefined;
  const facts = (payload.facts as Array<Record<string, unknown>> | undefined) ??
    [];
  const lines: string[] = [];
  if (snapshot) {
    lines.push(
      `memory: active=${snapshot.active ?? 0} ` +
        `conflict=${snapshot.conflict ?? 0} ` +
        `stale=${snapshot.stale ?? 0} ` +
        `total=${snapshot.total_facts ?? 0}`,
    );
  }
  for (const fact of facts.slice(0, 80)) {
    lines.push(
      `fact ${String(fact.fact_id)} ${String(fact.subject)} ` +
        `${String(fact.predicate)}=${String(fact.value)} ` +
        `status=${String(fact.status ?? "unknown")}`,
    );
  }
  if (facts.length === 0 && snapshot) {
    lines.push("no memory facts");
  }
  return lines;
}

export function formatAssetsSummary(
  data: Record<string, unknown>,
): string[] {
  const lines: string[] = [];
  const tools = (data.tools as Array<Record<string, unknown>> | undefined) ?? [];
  const skills = (data.skills as Array<Record<string, unknown>> | undefined) ?? [];
  const mcp = (data.mcp as Array<Record<string, unknown>> | undefined) ?? [];
  lines.push(`tools: ${tools.length}`);
  for (const tool of tools.slice(0, 12)) {
    lines.push(`  ${String(tool.tool_ref)} ${String(tool.status)}`);
  }
  lines.push(`skills: ${skills.length}`);
  for (const skill of skills.slice(0, 12)) {
    lines.push(
      `  ${String(skill.skill_ref)}@${String(skill.version)} runner=${String(
        skill.runner,
      )}`,
    );
  }
  lines.push(`mcp: ${mcp.length}`);
  for (const server of mcp.slice(0, 8)) {
    lines.push(
      `  ${String(server.server_id)} ${String(server.kind)} ${String(
        server.status,
      )}`,
    );
  }
  const storage = data.storage as Record<string, unknown> | undefined;
  if (storage) {
    lines.push(`storage: ${JSON.stringify(storage)}`);
  }
  return lines;
}
