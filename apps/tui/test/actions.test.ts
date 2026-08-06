import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AgentEvent } from "@veridix/sdk-typescript";
import {
  completeSlashCommand,
  exportReportBundle,
  firstPendingApproval,
  formatAssetsSummary,
  formatDiagnosticsSummary,
  formatGraphAscii,
  formatMemoryApiAscii,
  formatMemoryAscii,
  nextHistoryIndex,
  reportFileName,
  SLASH_COMMANDS,
} from "../src/actions.js";

const tempDirs: string[] = [];

afterEach(async () => {
  for (const dir of tempDirs.splice(0)) {
    await rm(dir, { recursive: true, force: true });
  }
});

describe("firstPendingApproval", () => {
  it("returns the first requested approval", () => {
    const approvals = [
      { approval_id: "a_1", state: "decided" },
      { approval_id: "a_2", state: "requested" },
      { approval_id: "a_3", state: "requested" },
    ];

    expect(firstPendingApproval(approvals)?.approval_id).toBe("a_2");
  });

  it("returns undefined without requested approvals", () => {
    expect(firstPendingApproval([])).toBeUndefined();
  });
});

describe("formatDiagnosticsSummary", () => {
  it("extracts tool digest and connector statuses", () => {
    const summary = formatDiagnosticsSummary({
      tool_environment: {
        digest: "env_digest_123",
      },
      connectors: {
        zap: { status: "ok" },
        caido: { status: "unreachable" },
        burp: { status: "not_configured" },
      },
    });

    expect(summary.toolDigest).toBe("env_digest_123");
    expect(summary.connectors).toEqual({
      zap: "ok",
      caido: "unreachable",
      burp: "not_configured",
    });
  });
});

describe("formatAssetsSummary", () => {
  it("lists tools, skills, mcp and storage", () => {
    const lines = formatAssetsSummary({
      tools: [{ tool_ref: "nmap.scan", status: "available" }],
      skills: [
        {
          skill_ref: "web.discovery",
          version: "0.1.0",
          runner: "browser",
        },
      ],
      mcp: [{ server_id: "mcp_caido", kind: "container", status: "ok" }],
      storage: { vector_store: { type: "sqlite" } },
    });

    expect(lines[0]).toBe("tools: 1");
    expect(lines.join("\n")).toContain("web.discovery@0.1.0");
    expect(lines.join("\n")).toContain("mcp_caido");
    expect(lines.join("\n")).toContain("sqlite");
  });
});

describe("formatGraphAscii", () => {
  it("formats nodes, handoffs and metrics into text rows", () => {
    const events = [
      {
        event_id: "e1",
        event_type: "graph.node.completed",
        payload: { node_id: "scanner", status: "succeeded" },
      },
      {
        event_id: "e2",
        event_type: "graph.handoff",
        payload: {
          from_node: "scanner",
          to_node: "verifier",
          fact_refs: ["f1", "f2"],
        },
      },
    ] as unknown as AgentEvent[];

    const lines = formatGraphAscii(events, {
      metrics: [
        {
          handoffs: 1,
          dead_letters: 0,
          duplicate_actions: 0,
          path_efficiency: 0.67,
        },
      ],
    });

    expect(lines.join("\n")).toContain("scanner: succeeded");
    expect(lines.join("\n")).toContain("scanner -> verifier facts=2");
    expect(lines.join("\n")).toContain("path_efficiency=0.67");
  });
});

describe("formatMemoryAscii", () => {
  it("formats memory snapshot and facts into text rows", () => {
    const events = [
      {
        event_id: "e1",
        event_type: "context.projection",
        payload: {
          memory: {
            snapshot: {
              active: 1,
              conflict: 0,
              stale: 1,
              total_facts: 2,
            },
            facts: [
              {
                fact_id: "f1",
                subject: "/admin",
                predicate: "reachable",
                value: "true",
                status: "active",
              },
              {
                fact_id: "f2",
                subject: "/old",
                predicate: "reachable",
                value: "true",
                status: "stale",
                expires_at: "2026-07-02T00:00:00Z",
              },
            ],
          },
        },
      },
    ] as unknown as AgentEvent[];

    const lines = formatMemoryAscii(events);

    expect(lines.join("\n")).toContain("stale=1 total=2");
    expect(lines.join("\n")).toContain("reachable=true status=stale");
    expect(lines.join("\n")).toContain("expires=2026-07-02T00:00:00Z");
  });
});

describe("formatMemoryApiAscii", () => {
  it("formats control-plane memory payload into text rows", () => {
    const lines = formatMemoryApiAscii({
      snapshot: {
        active: 3,
        conflict: 1,
        stale: 0,
        total_facts: 4,
      },
      facts: [
        {
          fact_id: "f_api",
          subject: "/admin",
          predicate: "accepts_role",
          value: "owner",
          status: "active",
        },
      ],
    });

    expect(lines.join("\n")).toContain("active=3 conflict=1 stale=0 total=4");
    expect(lines.join("\n")).toContain("accepts_role=owner status=active");
  });

  it("includes /memory in slash command completion", () => {
    expect(SLASH_COMMANDS).toContain("/memory");
    expect(SLASH_COMMANDS).toContain("/memory-record");
    expect(SLASH_COMMANDS).toContain("/memory-fix");
    expect(SLASH_COMMANDS).toContain("/memory-clear");
  });
});

describe("exportReportBundle", () => {
  it("writes the report zip to the output directory", async () => {
    const outDir = await mkdtemp(path.join(os.tmpdir(), "veridix-tui-"));
    tempDirs.push(outDir);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response("zip-bytes", {
          status: 200,
          headers: { "content-type": "application/zip" },
        }),
      ),
    );

    const outPath = await exportReportBundle(
      "run_1",
      "http://control.test",
      outDir,
    );
    vi.unstubAllGlobals();

    expect(path.basename(outPath)).toBe(reportFileName("run_1"));
    expect(await readFile(outPath, "utf8")).toBe("zip-bytes");
  });
});

describe("completeSlashCommand", () => {
  it("completes a unique prefix to a full command", () => {
    expect(completeSlashCommand("/hel", SLASH_COMMANDS).draft).toBe("/help");
  });

  it("returns a hint for ambiguous prefixes", () => {
    const result = completeSlashCommand("/skill", SLASH_COMMANDS);
    expect(result.draft).toBeUndefined();
    expect(result.hint).toContain("匹配");
  });
});

describe("nextHistoryIndex", () => {
  it("cycles backward and forward through history", () => {
    expect(nextHistoryIndex(-1, -1, 3)).toBe(2);
    expect(nextHistoryIndex(2, 1, 3)).toBe(-1);
    expect(nextHistoryIndex(-1, 1, 3)).toBe(0);
  });
});
