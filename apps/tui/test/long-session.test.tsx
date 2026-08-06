import { afterEach, describe, expect, it, vi } from "vitest";
import { render } from "ink-testing-library";


const instances: Array<{ unmount: () => void; stdin: { write: (chunk: string) => boolean } }> = [];

afterEach(() => {
  vi.unstubAllGlobals();
  for (const instance of instances.splice(0)) {
    instance.unmount();
  }
});


function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
}


function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}


function makeFetchMock() {
  const run = {
    run_id: "run_long_1",
    mission_id: "mission_long_1",
    status: "running",
    event_count: 12,
    created_at: "2026-08-05T00:00:00Z",
  };
  const events = [
    {
      event_id: "ev_1",
      event_type: "run.queued",
      payload: { mission_id: "mission_long_1" },
    },
    {
      event_id: "ev_2",
      event_type: "tool.completed",
      payload: { tool: "web.nikto.scan", exit_code: 0 },
    },
    {
      event_id: "ev_3",
      event_type: "context.projection",
      payload: {
        knowledge: { included: [] },
        skills: { included: ["web-nikto"] },
        mcp: { included: [] },
      },
    },
    {
      event_id: "ev_4",
      event_type: "graph.node.completed",
      payload: { node_id: "scanner", status: "succeeded" },
    },
  ];

  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/v1/runs")) {
      return json([run]);
    }
    if (url.includes("/api/v1/runs/run_long_1/events")) {
      return json(events);
    }
    if (url.includes("/api/v1/runs/run_long_1/web-observations")) {
      return json([]);
    }
    if (url.includes("/api/v1/runs/run_long_1/findings")) {
      return json([
        {
          finding_id: "finding_long_1",
          vuln_category: "Exposure",
          endpoint: "/admin",
          status: "verified",
        },
      ]);
    }
    if (url.includes("/api/v1/runs/run_long_1/approvals")) {
      return json([
        {
          approval_id: "approval_1",
          state: "requested",
          tool_ref: "web.sqlmap.scan",
          risk_level: "L2",
        },
      ]);
    }
    if (url.includes("/api/v1/runs/run_long_1/evidence")) {
      return json([]);
    }
    if (url.includes("/api/v1/runs/run_long_1/graph-metrics")) {
      return json({ metrics: [] });
    }
    if (url.endsWith("/api/v1/runs/run_long_1")) {
      return json(run);
    }
    if (url.endsWith("/api/v1/diagnostics")) {
      return json({
        providers: [
          {
            provider_id: "deepseek",
            model: "deepseek-v4-flash",
            endpoint: "https://api.deepseek.com/v1",
            status: "ok",
          },
        ],
        worker: { status: "ok" },
        tools: [],
        storage: {},
        connectors: {},
        components: {
          control_plane: { status: "ok", detail: "http://control.test" },
          deepseek_provider: {
            status: "ok",
            detail: "deepseek-v4-flash",
          },
          worker: { status: "ok", detail: "agent-worker" },
        },
      });
    }
    if (url.includes("/api/v1/runtime/skills")) {
      return json([
        {
          skill_ref: "web-nikto",
          name: "Web Nikto",
          version: "1.0",
          required_runner: "container",
          risk_level: "L3",
          description: "nikto web server scan",
        },
      ]);
    }
    if (url.includes("/api/v1/runtime/mcp")) {
      return json([]);
    }
    if (url.includes("/api/v1/runtime/tool-packs")) {
      return json([]);
    }
    if (url.includes("/api/v1/audit-logs")) {
      return json([]);
    }
    if (url.includes("/api/v1/remote/nodes")) {
      return json([]);
    }
    if (url.includes("/api/v1/acceptance")) {
      return json({
        gates: { overall: "passed", rows: [] },
        rag: { rows: [] },
        profile_engineering: {
          deterministic: { overall: "passed" },
          real_preset: {},
          real_presets: {},
          external_fixture: { overall: "pending" },
          preset_fixtures: { overall: "passed", preset_count: 10 },
          preset_count: 10,
        },
        readiness: { overall: "ready" },
        tool_smoke: { rows: [] },
      });
    }
    if (url.includes("/api/v1/runtime/loop-profiles")) {
      return json({});
    }
    if (url.includes("/api/v1/runtime/loop-presets")) {
      return json({});
    }
    if (url.includes("/api/v1/knowledge")) {
      return json([]);
    }
    if (url.includes("/api/v1/memory")) {
      return json({
        snapshot: { total_facts: 1, active: 1, conflict: 0, stale: 0 },
        facts: [
          {
            fact_id: "fact_long_1",
            subject: "/admin",
            predicate: "accepts_role",
            value: "owner",
            status: "active",
          },
        ],
        summaries: [],
      });
    }
    if (url.includes("/api/v1/sessions")) {
      return json([
        {
          session_id: "session_long_1",
          title: "long session",
          run_id: "run_long_1",
          status: "running",
        },
      ]);
    }
    if (url.includes("/api/v1/vulnerabilities")) {
      return json([
        {
          finding_id: "finding_long_1",
          severity: "high",
          vuln_category: "SQLi",
          endpoint: "/id",
        },
      ]);
    }
    if (url.includes("/api/v1/risk")) {
      return json({
        risk_score: 7,
        total_findings: 1,
        open_count: 1,
        severity_counts: { high: 1 },
      });
    }
    if (url.includes("/api/v1/missions")) {
      return json([]);
    }
    return json({});
  });
}


async function typeCommand(
  stdin: { write: (chunk: string) => boolean },
  command: string,
) {
  for (const char of `/${command}`) {
    stdin.write(char);
    await sleep(8);
  }
  stdin.write("\r");
  await sleep(100);
}


describe("TUI long interaction session", () => {
  it("navigates resource views and run detail over a sustained session", async () => {
    process.env.VERIDIX_CONTROL_URL = "http://control.test";
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    const { App } = await import("../src/index.js");
    const instance = render(<App />);
    instances.push(instance);
    await sleep(350);
    instance.stdin.write("\r");
    await sleep(100);

    let frame = instance.lastFrame() ?? "";
    expect(frame, JSON.stringify(frame)).toContain("run_long_1");
    expect(frame).toContain("运行列表");

    await typeCommand(instance.stdin, "skills");
    frame = instance.lastFrame() ?? "";
    expect(frame).toContain("技能");
    expect(frame).toContain("web-nikto");

    await typeCommand(instance.stdin, "health");
    frame = instance.lastFrame() ?? "";
    expect(frame).toContain("健康");
    expect(frame).toContain("deepseek");

    await typeCommand(instance.stdin, "loop-presets");
    frame = instance.lastFrame() ?? "";
    expect(frame).toContain("Loop Presets");

    await typeCommand(instance.stdin, "sessions");
    frame = instance.lastFrame() ?? "";
    expect(frame).toContain("会话");
    expect(frame).toContain("long session");

    await typeCommand(instance.stdin, "vulns");
    frame = instance.lastFrame() ?? "";
    expect(frame).toContain("漏洞");
    expect(frame).toContain("SQLi");

    await typeCommand(instance.stdin, "risk");
    frame = instance.lastFrame() ?? "";
    expect(frame).toContain("风险");
    expect(frame).toContain("风险评分");

    await typeCommand(instance.stdin, "acceptance");
    frame = instance.lastFrame() ?? "";
    expect(frame).toContain("验收");
    expect(frame).toContain("门禁");

    instance.stdin.write("q");
    await sleep(60);
    instance.stdin.write("\r");
    await sleep(500);
    frame = instance.lastFrame() ?? "";
    expect(frame).toContain("运行详情");
    expect(frame).toContain("活动");
    expect(frame).toContain("web.sqlmap.scan");

    instance.stdin.write("G");
    await sleep(60);
    frame = instance.lastFrame() ?? "";
    expect(frame).toContain("图");

    instance.stdin.write("M");
    await sleep(60);
    frame = instance.lastFrame() ?? "";
    expect(frame).toContain("记忆");

    instance.stdin.write("d");
    await sleep(60);
    frame = instance.lastFrame() ?? "";
    expect(frame).toContain("审批");
    expect(frame).toContain("web.sqlmap.scan");

    instance.stdin.write("q");
    await sleep(60);
    frame = instance.lastFrame() ?? "";
    expect(frame).toContain("运行列表");
  });

  it("supports slash command autocomplete and history", async () => {
    process.env.VERIDIX_CONTROL_URL = "http://control.test";
    vi.stubGlobal("fetch", makeFetchMock());
    const { App } = await import("../src/index.js");
    const instance = render(<App />);
    instances.push(instance);
    await sleep(200);

    instance.stdin.write("/");
    await sleep(20);
    for (const char of "hel") {
      instance.stdin.write(char);
      await sleep(10);
    }
    instance.stdin.write("\t");
    await sleep(80);
    instance.stdin.write("\r");
    await sleep(120);
    let frame = instance.lastFrame() ?? "";
    expect(frame).toContain("帮助");

    instance.stdin.write("q");
    await sleep(60);
    await typeCommand(instance.stdin, "skills");
    frame = instance.lastFrame() ?? "";
    expect(frame).toContain("技能");

    instance.stdin.write("/");
    await sleep(30);
    instance.stdin.write("\u001B[A");
    await sleep(80);
    instance.stdin.write("\r");
    await sleep(120);
    frame = instance.lastFrame() ?? "";
    expect(frame).toContain("技能");
  });
});
