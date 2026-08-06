#!/usr/bin/env node
import { useEffect, useState } from "react";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Box, Text, useApp, useInput, render } from "ink";
import {
  ControlClient,
  type AgentEvent,
  type ApprovalRequest,
  type Finding,
  type RunState,
  type WebObservation,
} from "@veridix/sdk-typescript";
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
  SLASH_COMMANDS,
} from "./actions.js";

const VERSION = "0.1.0";
if (process.argv.includes("--version") || process.argv.includes("-v")) {
  console.log(VERSION);
  process.exit(0);
}
if (process.argv.includes("--help") || process.argv.includes("-h")) {
  console.log(
    "veridix-tui: Veridix 运行控制台\n\n" +
      "列表：j/k 选择，Enter 打开，n 新建任务，f 过滤，q 退出\n" +
      "详情：q 返回，Tab/方向键切换视图，p/r/c 暂停/恢复/取消\n" +
      "       m 发送指令，F/T fork/takeover，E 导出报告，a/x 审批，? 帮助\n" +
      "/ 斜杠命令：" + SLASH_COMMANDS.join(" ") + "\n",
  );
  process.exit(0);
}

const baseUrl = process.env.VERIDIX_CONTROL_URL ?? "http://127.0.0.1:8787";
const client = new ControlClient(baseUrl);

type View =
  | "events"
  | "observations"
  | "findings"
  | "evidence"
  | "approvals"
  | "graph"
  | "memory"
  | "assets";

const VIEWS: Array<{ id: View; label: string; key: string }> = [
  { id: "events", label: "活动", key: "e" },
  { id: "graph", label: "图", key: "G" },
  { id: "memory", label: "记忆", key: "M" },
  { id: "observations", label: "Web", key: "o" },
  { id: "findings", label: "发现", key: "f" },
  { id: "evidence", label: "证据", key: "g" },
  { id: "approvals", label: "审批", key: "d" },
  { id: "assets", label: "资产", key: "A" },
];

function statusColor(status: string): string {
  switch (status) {
    case "running":
    case "requested":
    case "claimed":
    case "in_progress":
      return "cyan";
    case "succeeded":
    case "verified":
    case "supported":
    case "approved":
      return "green";
    case "failed":
    case "cancelled":
    case "rejected":
    case "expired":
      return "red";
    case "paused":
    case "waiting":
    case "pending":
      return "yellow";
    default:
      return "white";
  }
}

function eventColor(type: string): string {
  if (type.startsWith("tool.")) {
    return "cyan";
  }
  if (type.startsWith("run.")) {
    return "green";
  }
  if (type.startsWith("graph.")) {
    return "magenta";
  }
  if (type.startsWith("model.")) {
    return "blue";
  }
  if (type.startsWith("context.")) {
    return "yellow";
  }
  return "white";
}

function eventMarker(type: string): string {
  if (type.includes("completed") || type.includes("succeeded")) {
    return "✓";
  }
  if (type.includes("failed") || type.includes("cancelled")) {
    return "×";
  }
  if (type.includes("proposed") || type.includes("requested")) {
    return "?";
  }
  if (type.includes("handoff")) {
    return "→";
  }
  if (type.startsWith("tool.")) {
    return "⚙";
  }
  if (type.startsWith("model.")) {
    return "◆";
  }
  if (type.startsWith("graph.")) {
    return "◈";
  }
  return "•";
}

function eventSummary(event: AgentEvent): string {
  const payload = event.payload as Record<string, unknown>;
  if (event.event_type === "model.turn.started") {
    return `turn ${String(payload.turn ?? "?")}`;
  }
  if (event.event_type.startsWith("tool.")) {
    return `${String(payload.tool ?? "")} ${JSON.stringify(
      payload.arguments ?? payload.args ?? "",
    ).slice(0, 70)}`;
  }
  if (event.event_type === "observation.ingested") {
    return `parsed=${
      (payload.parsed_observations as unknown[] | undefined)?.length ?? 0
    } ${String(payload.tool ?? "")}`;
  }
  if (event.event_type === "graph.handoff") {
    return `${String(payload.from_node ?? "")}->${String(
      payload.to_node ?? "",
    )} facts=${(payload.fact_refs as unknown[] | undefined)?.length ?? 0}`;
  }
  if (event.event_type === "context.projection") {
    return `knowledge=${String(
      (payload.knowledge as { included?: unknown[] } | undefined)?.included
        ?.length ?? 0,
    )} skills=${String(
      (payload.skills as { included?: unknown[] } | undefined)?.included
        ?.length ?? 0,
    )} mcp=${String(
      (payload.mcp as { included?: unknown[] } | undefined)?.included
        ?.length ?? 0,
    )}`;
  }
  if (event.event_type === "run.submitted") {
    return String(payload.user_input ?? "");
  }
  if (event.event_type === "run.paused" || event.event_type === "run.cancelled") {
    return String(payload.reason ?? "");
  }
  return JSON.stringify(payload).slice(0, 80);
}

function Spinner() {
  const frames = ["|", "/", "-", "\\"];
  const [index, setIndex] = useState(0);
  useEffect(() => {
    const timer = setInterval(
      () => setIndex((current) => (current + 1) % frames.length),
      100,
    );
    return () => clearInterval(timer);
  }, []);
  return <Text color="cyan">{frames[index]}</Text>;
}

function RunList({
  runs,
  selected,
}: {
  runs: RunState[];
  selected: number;
}) {
  return (
    <Box flexDirection="column" width="100%">
      <Box
        borderStyle="single"
        borderColor="cyan"
        flexDirection="column"
        paddingX={1}
      >
        <Text bold color="cyan">
          运行列表
        </Text>
        <Text dimColor>j/k 选择  Enter 打开</Text>
      </Box>
      <Box flexDirection="column" marginTop={1}>
        {runs.length === 0 ? (
          <Text dimColor>暂无运行，先创建任务。</Text>
        ) : (
          runs.map((run, index) => {
            const active = index === selected;
            return (
              <Box key={run.run_id} flexDirection="row">
                <Text
                  color={active ? "black" : statusColor(run.status)}
                  backgroundColor={active ? "cyan" : undefined}
                  bold={active}
                >
                  {active ? ">" : " "} {String(index + 1).padStart(2, " ")}{" "}
                  {run.run_id.slice(0, 16).padEnd(16, " ")}
                </Text>
                <Text
                  color={statusColor(run.status)}
                  backgroundColor={active ? "cyan" : undefined}
                >
                  {run.status.padEnd(9, " ")}
                </Text>
                <Text
                  dimColor={!active}
                  color={active ? "black" : undefined}
                  backgroundColor={active ? "cyan" : undefined}
                >
                  {String(run.event_count).padStart(3, " ")} ev
                </Text>
              </Box>
            );
          })
        )}
      </Box>
    </Box>
  );
}

function TabBar({ view }: { view: View }) {
  return (
    <Box flexDirection="row" marginTop={1}>
      {VIEWS.map((item) => (
        <Text
          key={item.id}
          color={view === item.id ? "black" : "white"}
          backgroundColor={view === item.id ? "cyan" : undefined}
          bold={view === item.id}
          dimColor={view !== item.id}
        >
          {view === item.id ? "[" : " "}
          {item.key} {item.label}
          {view === item.id ? "]" : " "}
        </Text>
      ))}
    </Box>
  );
}

function ActivityFeed({ events }: { events: AgentEvent[] }) {
  const rows = events.slice(-56);
  if (rows.length === 0) {
    return <Text dimColor>暂无事件</Text>;
  }
  return (
    <Box flexDirection="column">
      {rows.map((event) => (
        <Text key={event.event_id} wrap="truncate-end">
          <Text color={eventColor(event.event_type)}>
            {eventMarker(event.event_type)}
          </Text>{" "}
          <Text dimColor>{String(event.sequence).padStart(3, " ")}</Text>{" "}
          <Text color={eventColor(event.event_type)}>
            {event.event_type.replace(/\./g, ".").slice(0, 26).padEnd(26, " ")}
          </Text>
          <Text dimColor>{eventSummary(event)}</Text>
        </Text>
      ))}
    </Box>
  );
}

function InfoPanel({
  run,
  approvals,
  graphMetrics,
}: {
  run: RunState;
  approvals: ApprovalRequest[];
  graphMetrics: Record<string, unknown> | null;
}) {
  const metrics = (graphMetrics?.metrics as
    | Array<Record<string, unknown>>
    | undefined) ?? [];
  const last = metrics[metrics.length - 1];
  const pending = approvals.filter((approval) => approval.state === "requested");
  return (
    <Box
      width={54}
      flexShrink={0}
      borderStyle="single"
      borderColor="white"
      flexDirection="column"
      paddingX={1}
    >
      <Text bold>状态</Text>
      <Text color={statusColor(run.status)}>{run.status}</Text>
      <Text dimColor>events={run.event_count}</Text>
      <Text dimColor>mission={run.mission_id.slice(0, 14)}</Text>
      <Text dimColor>created={run.created_at?.slice(0, 16)}</Text>
      <Box marginTop={1}>
        <Text bold>图指标</Text>
      </Box>
      {last ? (
        <>
          <Text dimColor>handoffs={String(last.handoffs)}</Text>
          <Text dimColor>dead_letters={String(last.dead_letters)}</Text>
          <Text dimColor>duplicates={String(last.duplicate_actions)}</Text>
          <Text color="green">
            efficiency={String(last.path_efficiency)}
          </Text>
        </>
      ) : (
        <Text dimColor>暂无</Text>
      )}
      <Box marginTop={1}>
        <Text bold>审批</Text>
      </Box>
      {pending.length === 0 ? (
        <Text dimColor>无待办</Text>
      ) : (
        pending.slice(0, 3).map((approval) => (
          <Text key={approval.approval_id} color="yellow" wrap="truncate-end">
            {approval.tool_ref.slice(0, 16)} {approval.risk_level}
          </Text>
        ))
      )}
      {pending.length > 3 ? (
        <Text dimColor>+{pending.length - 3} 更多</Text>
      ) : null}
    </Box>
  );
}

function DetailContent({
  view,
  events,
  observations,
  findings,
  evidence,
  approvals,
  graphMetrics,
  assets,
  memoryData,
}: {
  view: View;
  events: AgentEvent[];
  observations: WebObservation[];
  findings: Finding[];
  evidence: Array<Record<string, unknown>>;
  approvals: ApprovalRequest[];
  graphMetrics: Record<string, unknown> | null;
  assets: Record<string, unknown> | null;
  memoryData: Record<string, unknown> | null;
}) {
  if (view === "events") {
    return <ActivityFeed events={events} />;
  }
  if (view === "graph") {
    return (
      <Box flexDirection="column">
        {formatGraphAscii(events, graphMetrics).map((line, index) => (
          <Text key={index} color="magenta" dimColor>
            {line}
          </Text>
        ))}
      </Box>
    );
  }
  if (view === "memory") {
    const lines = memoryData
      ? formatMemoryApiAscii(memoryData)
      : formatMemoryAscii(events);
    return (
      <Box flexDirection="column">
        {lines.map((line, index) => (
          <Text key={index} dimColor>
            {line}
          </Text>
        ))}
      </Box>
    );
  }
  if (view === "assets") {
    return (
      <Box flexDirection="column">
        {assets
          ? formatAssetsSummary(assets).map((line, index) => (
              <Text key={index} dimColor>
                {line}
              </Text>
            ))
          : (
              <Text dimColor>资产快照未就绪</Text>
            )}
      </Box>
    );
  }
  if (view === "observations") {
    return (
      <Box flexDirection="column">
        {observations.length === 0 ? (
          <Text dimColor>暂无 Web 观测</Text>
        ) : (
          observations.slice(-40).map((observation) => (
            <Text key={observation.request_id} wrap="truncate-end">
              <Text color="cyan" bold>
                {observation.method.padEnd(5, " ")}
              </Text>{" "}
              <Text
                color={
                  observation.status_code >= 400
                    ? "red"
                    : observation.status_code >= 300
                      ? "yellow"
                      : "green"
                }
              >
                {String(observation.status_code).padStart(3, " ")}
              </Text>{" "}
              <Text dimColor>{observation.url}</Text>
            </Text>
          ))
        )}
      </Box>
    );
  }
  if (view === "findings") {
    return (
      <Box flexDirection="column">
        {findings.length === 0 ? (
          <Text dimColor>暂无发现</Text>
        ) : (
          findings.map((finding) => (
            <Text key={finding.finding_id} wrap="truncate-end">
              <Text color={statusColor(finding.status)} bold>
                {finding.status.padEnd(9, " ")}
              </Text>{" "}
              <Text color="yellow">{finding.vuln_category.padEnd(16, " ")}</Text>{" "}
              <Text dimColor>{finding.endpoint}</Text>
            </Text>
          ))
        )}
      </Box>
    );
  }
  if (view === "evidence") {
    return (
      <Box flexDirection="column">
        {evidence.length === 0 ? (
          <Text dimColor>暂无证据记录</Text>
        ) : (
          evidence.map((item) => (
            <Text key={String(item.evidence_id)} wrap="truncate-end">
              <Text color="cyan">
                {String(item.source_type).padEnd(16, " ")}
              </Text>{" "}
              <Text dimColor>{String(item.action_ref)}</Text>
            </Text>
          ))
        )}
      </Box>
    );
  }
  return (
    <Box flexDirection="column">
      {approvals.length === 0 ? (
        <Text dimColor>暂无审批事件</Text>
      ) : (
        approvals.map((approval) => (
          <Text key={approval.approval_id} wrap="truncate-end">
            <Text color={statusColor(approval.state)} bold>
              {approval.state.padEnd(9, " ")}
            </Text>{" "}
            <Text color="yellow">{approval.risk_level.padEnd(8, " ")}</Text>{" "}
            <Text dimColor>{approval.tool_ref}</Text>
          </Text>
        ))
      )}
    </Box>
  );
}

function RunDetail({
  run,
  events,
  observations,
  findings,
  evidence,
  approvals,
  exportedPath,
  graphMetrics,
  assets,
  memoryData,
  view,
}: {
  run: RunState;
  events: AgentEvent[];
  observations: WebObservation[];
  findings: Finding[];
  evidence: Array<Record<string, unknown>>;
  approvals: ApprovalRequest[];
  exportedPath: string | null;
  graphMetrics: Record<string, unknown> | null;
  assets: Record<string, unknown> | null;
  memoryData: Record<string, unknown> | null;
  view: View;
}) {
  const pending = firstPendingApproval(approvals);
  const activeView = VIEWS.find((item) => item.id === view) ?? VIEWS[0];
  return (
    <Box flexDirection="column" width="100%">
      <Box
        borderStyle="single"
        borderColor="cyan"
        flexDirection="column"
        paddingX={1}
      >
        <Text>
          <Text bold color="cyan">
            {run.run_id}
          </Text>{" "}
          <Text color={statusColor(run.status)} bold>
            {run.status}
          </Text>{" "}
          <Text dimColor>
            {run.event_count} 事件 · {activeView.label} 视图
          </Text>
        </Text>
        <Text dimColor>
          mission={run.mission_id} created={run.created_at}
          {run.stop_reason ? ` stop=${run.stop_reason}` : ""}
        </Text>
      </Box>
      {pending ? (
        <Box marginTop={1}>
          <Text color="yellow" bold>
            ! 待审批 {pending.tool_ref} ({pending.risk_level})  a=批准 x=拒绝
          </Text>
        </Box>
      ) : null}
      {exportedPath ? (
        <Box marginTop={1}>
          <Text color="green">已导出：{exportedPath}</Text>
        </Box>
      ) : null}
      <TabBar view={view} />
      <Box flexDirection="row" marginTop={1}>
        <Box flexGrow={1} paddingRight={1}>
          <DetailContent
            view={view}
            events={events}
            observations={observations}
            findings={findings}
            evidence={evidence}
            approvals={approvals}
            graphMetrics={graphMetrics}
            assets={assets}
            memoryData={memoryData}
          />
        </Box>
        <InfoPanel run={run} approvals={approvals} graphMetrics={graphMetrics} />
      </Box>
    </Box>
  );
}

function Overview({
  diagnostics,
  assets,
  runs,
}: {
  diagnostics: Record<string, unknown> | null;
  assets: Record<string, unknown> | null;
  runs: RunState[];
}) {
  const summary = diagnostics
    ? formatDiagnosticsSummary(diagnostics)
    : { toolDigest: "", connectors: {} };
  const active = runs.filter((run) =>
    ["requested", "running", "claimed", "paused"].includes(run.status),
  ).length;
  const done = runs.filter((run) =>
    ["succeeded", "failed", "cancelled"].includes(run.status),
  ).length;
  return (
    <Box flexDirection="column" width="100%">
      <Box
        borderStyle="single"
        borderColor="cyan"
        flexDirection="column"
        paddingX={1}
      >
        <Text bold color="cyan">
          环境概览
        </Text>
        <Text dimColor>控制面 {baseUrl}</Text>
        <Text>
          运行 <Text bold>{runs.length}</Text> 进行中{" "}
          <Text color="cyan" bold>
            {active}
          </Text>{" "}
          已完成{" "}
          <Text color="green" bold>
            {done}
          </Text>
        </Text>
        <Text dimColor>
          工具环境 digest: {summary.toolDigest || "none"}
        </Text>
        <Text dimColor>
          连接器:{" "}
          {Object.entries(summary.connectors)
            .map(([name, status]) => `${name}=${status}`)
            .join("  ")}
        </Text>
      </Box>
      <Box
        borderStyle="single"
        borderColor="white"
        flexDirection="column"
        marginTop={1}
        paddingX={1}
      >
        <Text bold>快捷键</Text>
        <Text dimColor>
          j/k 选择  Enter 打开  q 返回/退出  Tab 切换视图  ? 帮助
        </Text>
        <Text dimColor>
          / 打开命令  p/r/c 暂停/恢复/取消  m 发送指令  F/T fork/takeover  E 导出  a/x 审批
        </Text>
      </Box>
      {assets ? (
        <Box
          borderStyle="single"
          borderColor="white"
          flexDirection="column"
          marginTop={1}
          paddingX={1}
        >
          <Text bold>资产摘要</Text>
          {formatAssetsSummary(assets).map((line, index) => (
            <Text key={index} dimColor>
              {line}
            </Text>
          ))}
        </Box>
      ) : null}
    </Box>
  );
}

function Footer({ detail, filter }: { detail: boolean; filter: string }) {
  return (
    <Box marginTop={1} justifyContent="space-between" width="100%">
      <Text dimColor>
        {detail
          ? "q 返回  e/o/f/g/G/M/A 视图  p/r/c 控制  m 指令  a/x 审批  F/T 会话  E 导出"
          : `j/k 选择  Enter 打开  n 新建任务  f 过滤(${filter})  q 退出`}
      </Text>
      <Text dimColor>Veridix TUI v{VERSION}</Text>
    </Box>
  );
}

function HomeScreen({
  diagnostics,
  assets,
  runs,
}: {
  diagnostics: Record<string, unknown> | null;
  assets: Record<string, unknown> | null;
  runs: RunState[];
}) {
  const providers =
    (diagnostics?.providers as Array<Record<string, unknown>> | undefined) ?? [];
  const components =
    (diagnostics?.components as
      | Record<string, Record<string, unknown>>
      | undefined) ?? {};
  const storage = (diagnostics?.storage as Record<string, unknown> | undefined) ?? {};
  const toolHealth =
    String(
      (diagnostics?.tool_environment as Record<string, unknown> | undefined)
        ?.health ?? "missing",
    ) === "ok";
  const activeCount = runs.filter((run) =>
    ["requested", "running", "claimed", "paused"].includes(run.status),
  ).length;
  const okCount = Object.values(components).filter(
    (component) => component.status === "ok",
  ).length;
  const skills = (assets?.skills as unknown[] | undefined)?.length ?? 0;
  const mcp = (assets?.mcp as unknown[] | undefined)?.length ?? 0;

  return (
    <Box flexDirection="column" alignItems="center" marginTop={7} width="100%">
      <Text bold color="cyan">
        V E R I D I X
      </Text>
      <Text dimColor>授权安全测试与漏洞验证 Agent</Text>
      <Box marginTop={3} flexDirection="column" alignItems="center">
        <Text>
          <Text bold color="cyan">[ Enter ]</Text>  运行列表
        </Text>
        <Text>
          <Text bold color="green">[ n ]</Text>     新建任务
        </Text>
        <Text>
          <Text bold color="yellow">[ / ]</Text>     斜杠命令
        </Text>
        <Text>
          <Text bold color="red">[ q ]</Text>     退出
        </Text>
      </Box>
      <Box marginTop={3}>
        <Text dimColor>
          运行 {activeCount}/{runs.length}  ·  模型 {providers.length}  ·
          组件 {okCount}  ·  工具 {toolHealth ? "可用" : "未就绪"}  ·
          技能 {skills}  ·  MCP {mcp}  ·  存储 {storage.available ? "已就绪" : "未上报"}
        </Text>
      </Box>
    </Box>
  );
}

function ResourceView({
  view,
  diagnostics,
  assets,
  knowledge,
  memoryData,
  sessions,
  vulns,
  risk,
  tools,
  audit,
  health,
  loopProfiles,
  loopPresets,
  nodes,
  acceptance,
}: {
  view:
    | "providers"
    | "skills"
    | "mcp"
    | "nodes"
    | "acceptance"
    | "knowledge"
    | "memory"
    | "sessions"
    | "vulns"
    | "risk"
    | "tools"
    | "audit"
    | "health"
    | "loops"
    | "presets";
  diagnostics: Record<string, unknown> | null;
  assets: Record<string, unknown> | null;
  knowledge: Array<Record<string, unknown>> | null;
  memoryData: Record<string, unknown> | null;
  sessions: Array<Record<string, unknown>> | null;
  vulns: Array<Record<string, unknown>> | null;
  risk: Record<string, unknown> | null;
  tools: Array<Record<string, unknown>> | null;
  audit: Array<Record<string, unknown>> | null;
  health: Record<string, unknown> | null;
  loopProfiles: Array<Record<string, unknown>> | null;
  loopPresets: Array<Record<string, unknown>> | null;
  nodes: Array<Record<string, unknown>> | null;
  acceptance: Record<string, unknown> | null;
}) {
  const providers =
    (diagnostics?.providers as Array<Record<string, unknown>> | undefined) ?? [];
  const skills =
    (assets?.skills as Array<Record<string, unknown>> | undefined) ?? [];
  const mcpServers =
    (assets?.mcp as Array<Record<string, unknown>> | undefined) ?? [];
  const title =
    view === "providers"
      ? "模型供应商"
      : view === "skills"
        ? "技能"
                  : view === "mcp"
                    ? "MCP 服务器"
                    : view === "nodes"
                      ? "远程节点"
                      : view === "acceptance"
                        ? "验收"
                    : view === "knowledge"
            ? "知识库"
            : view === "memory"
              ? "项目记忆"
            : view === "sessions"
              ? "会话"
              : view === "vulns"
                ? "漏洞"
                : view === "risk"
                  ? "风险"
                  : view === "tools"
                    ? "工具"
                    : view === "audit"
                    ? "审计日志"
                      : view === "health"
                        ? "健康"
                        : view === "loops"
                          ? "Loop Profiles"
                          : "Loop Presets";
  return (
    <Box
      borderStyle="single"
      borderColor="cyan"
      flexDirection="column"
      paddingX={1}
      width="100%"
    >
      <Text bold color="cyan">
        {title} (q 返回)
      </Text>
      {view === "providers" ? (
        providers.length === 0 ? (
          <Text dimColor>暂无供应商</Text>
        ) : (
          providers.map((provider) => (
            <Text key={String(provider.provider_id)} wrap="truncate-end">
              <Text color="green" bold>
                {String(provider.status).padEnd(6, " ")}
              </Text>{" "}
              <Text color="cyan">{String(provider.provider_id).padEnd(18, " ")}</Text>{" "}
              <Text dimColor>
                {String(provider.model)} @ {String(provider.endpoint)}
              </Text>
            </Text>
          ))
        )
      ) : view === "skills" ? (
        skills.length === 0 ? (
          <Text dimColor>暂无技能</Text>
        ) : (
          skills.map((skill) => (
            <Text key={String(skill.skill_ref)} wrap="truncate-end">
              <Text color="cyan">{String(skill.skill_ref).padEnd(22, " ")}</Text>{" "}
              <Text dimColor>
                v{String(skill.version)} runner=
                {String(skill.required_runner ?? skill.runner ?? "-")} risk=
                {String(skill.risk_level ?? "L1")}
              </Text>
              {" "}
              <Text dimColor wrap="truncate-end">
                {String(
                  (skill.description as string | undefined) ??
                    (skill.trigger as string | undefined) ??
                    "",
                ).slice(0, 72)}
              </Text>
            </Text>
          ))
        )
      ) : view === "mcp" ? (
        mcpServers.length === 0 ? (
          <Text dimColor>暂无 MCP</Text>
        ) : (
          mcpServers.map((server) => (
            <Text key={String(server.server_id)} wrap="truncate-end">
              <Text color="cyan">{String(server.server_id).padEnd(18, " ")}</Text>{" "}
              <Text dimColor>
                {String(server.kind)} · {String(server.status)}
              </Text>
            </Text>
          ))
        )
      ) : view === "memory" ? (
        memoryData ? (
          formatMemoryApiAscii(memoryData).map((line, index) => (
            <Text key={index} dimColor>
              {line}
            </Text>
          ))
        ) : (
          <Text dimColor>加载记忆...</Text>
        )
      ) : view === "sessions" ? (
        sessions === null ? (
          <Text dimColor>加载会话...</Text>
        ) : sessions.length === 0 ? (
          <Text dimColor>暂无会话</Text>
        ) : (
          sessions.map((session) => (
            <Text key={String(session.session_id)} wrap="truncate-end">
              <Text color={String(session.status) === "running" ? "cyan" : "white"} bold>
                {String(session.status ?? "?").padEnd(10, " ")}
              </Text>{" "}
              <Text color="cyan">{String(session.title ?? "").padEnd(20, " ")}</Text>{" "}
              <Text dimColor>{String(session.run_id ?? "").slice(0, 14)}</Text>
            </Text>
          ))
        )
      ) : view === "vulns" ? (
        vulns === null ? (
          <Text dimColor>加载漏洞...</Text>
        ) : vulns.length === 0 ? (
          <Text dimColor>暂无漏洞</Text>
        ) : (
          vulns.map((vuln) => (
            <Text key={String(vuln.finding_id)} wrap="truncate-end">
              <Text color="red" bold>
                {String(vuln.severity ?? "medium").padEnd(9, " ")}
              </Text>{" "}
              <Text color="yellow">{String(vuln.vuln_category ?? "").padEnd(16, " ")}</Text>{" "}
              <Text dimColor>{String(vuln.endpoint ?? "")}</Text>
            </Text>
          ))
        )
      ) : view === "risk" ? (
        risk === null ? (
          <Text dimColor>加载风险...</Text>
        ) : (
          <>
            <Text>
              <Text bold>风险评分</Text>{" "}
              <Text color="yellow">{String(risk.risk_score ?? 0)}</Text>
            </Text>
            <Text>
              漏洞总数 <Text bold>{String(risk.total_findings ?? 0)}</Text> · 未关闭{" "}
              <Text color="yellow">{String(risk.open_count ?? 0)}</Text>
            </Text>
            <Text dimColor>
              严重度:{" "}
              {JSON.stringify(risk.severity_counts ?? {})}
            </Text>
          </>
        )
      ) : view === "nodes" ? (
        nodes === null ? (
          <Text dimColor>加载节点...</Text>
        ) : nodes.length === 0 ? (
          <Text dimColor>暂无远程节点，注册后显示在这里。</Text>
        ) : (
          nodes.map((node) => (
            <Text key={String(node.node_id)} wrap="truncate-end">
              <Text
                color={String(node.status) === "online" ? "green" : "red"}
                bold
              >
                {String(node.status).padEnd(8, " ")}
              </Text>{" "}
              <Text color="cyan">
                {String(node.node_id).padEnd(18, " ")}
              </Text>{" "}
              <Text dimColor>
                v{String(node.version)} 路{" "}
                {(node.capabilities as unknown[] | undefined)?.join(",") ?? "-"}
              </Text>
            </Text>
          ))
        )
      ) : view === "acceptance" ? (
        acceptance === null ? (
          <Text dimColor>加载验收...</Text>
        ) : (
          <>
            <Text bold color="green">
              门禁 {String((acceptance.gates as Record<string, unknown> | undefined)?.overall ?? "not_run")}
            </Text>
            {(
              (acceptance.gates as Record<string, unknown> | undefined)
                ?.rows as Array<Record<string, unknown>> | undefined ?? []
            ).map((row) => (
              <Text key={String(row.scenario)} wrap="truncate-end">
                <Text color="cyan">{String(row.scenario).padEnd(10, " ")}</Text>{" "}
                <Text color="green">{String(row.assertion).padEnd(8, " ")}</Text>{" "}
                <Text dimColor>verified {String(row.verified ?? "-")}</Text>
              </Text>
            ))}
            <Text bold color="cyan">
              RAG
            </Text>
            {(
              (acceptance.rag as Record<string, unknown> | undefined)
                ?.rows as Array<Record<string, unknown>> | undefined ?? []
            ).map((row) => (
              <Text key={String(row.rag_level)} wrap="truncate-end">
                <Text color="cyan">{String(row.rag_level).padEnd(14, " ")}</Text>{" "}
                hit {String(row.hit_rate)} p95 {String(row.p95_ms)}ms
              </Text>
            ))}
            <Text bold color="cyan">
              profile engineering
            </Text>
            {(() => {
              const profile = (acceptance.profile_engineering as
                | Record<string, unknown>
                | undefined) ?? {};
              const deterministic = (profile.deterministic as
                | Record<string, unknown>
                | undefined) ?? {};
              const real = (profile.real_preset as
                | Record<string, unknown>
                | undefined) ?? {};
              const baseline = (real.baseline as
                | Record<string, unknown>
                | undefined) ?? {};
              const preset = (real.preset as
                | Record<string, unknown>
                | undefined) ?? {};
              const baselineMean = (baseline.mean as
                | Record<string, unknown>
                | undefined) ?? {};
              const presetMean = (preset.mean as
                | Record<string, unknown>
                | undefined) ?? {};
              const realPresets = (profile.real_presets as
                | Record<string, Record<string, unknown>>
                | undefined) ?? {};
              const hostRecon = realPresets["host-recon"] ?? {};
              const externalFixture = (profile.external_fixture as
                | Record<string, unknown>
                | undefined) ?? {};
              const presetFixtures = (profile.preset_fixtures as
                | Record<string, unknown>
                | undefined) ?? {};
              return (
                <>
                  <Text wrap="truncate-end">
                    deterministic {String(deterministic.overall ?? "not_run")} ·{" "}
                    presets {String(profile.preset_count ?? 0)}
                  </Text>
                  <Text wrap="truncate-end">
                    {String(real.preset_id ?? "preset")} real{" "}
                    {String(real.overall ?? "not_run")}
                  </Text>
                  <Text wrap="truncate-end">
                    baseline actions {String(baselineMean.tool_calls ?? "-")} ·{" "}
                    preset actions {String(presetMean.tool_calls ?? "-")} ·{" "}
                    verified {String(presetMean.verified_findings ?? "-")}
                  </Text>
                  <Text wrap="truncate-end">
                    host-recon real {String(hostRecon.overall ?? "not_run")} ·{" "}
                    ad/cloud fixture {String(externalFixture.overall ?? "pending")}
                  </Text>
                  <Text wrap="truncate-end">
                    preset fixtures {String(presetFixtures.overall ?? "not_run")} ·{" "}
                    {String(presetFixtures.preset_count ?? 0)} presets
                  </Text>
                </>
              );
            })()}
            <Text bold color="yellow">
              readiness{" "}
              {String(
                (acceptance.readiness as Record<string, unknown> | undefined)
                  ?.overall ?? "not_run",
              )}
            </Text>
            <Text bold color="cyan">
              tool smoke
            </Text>
            {(
              (acceptance.tool_smoke as Record<string, unknown> | undefined)
                ?.rows as Array<Record<string, unknown>> | undefined ?? []
            ).map((row) => (
              <Text key={String(row.tool)} wrap="truncate-end">
                <Text color="cyan">{String(row.tool).padEnd(12, " ")}</Text>{" "}
                <Text color="green">{String(row.status).padEnd(6, " ")}</Text>{" "}
                <Text dimColor>{String(row.detail ?? "")}</Text>
              </Text>
            ))}
          </>
        )
      ) : view === "tools" ? (
        tools === null ? (
          <Text dimColor>Loading tools...</Text>
        ) : tools.length === 0 ? (
          <Text dimColor>No tool packs</Text>
        ) : (
          tools.slice(0, 20).map((pack) => (
            <Text key={String(pack.name)} wrap="truncate-end">
              <Text color="cyan">{String(pack.name).padEnd(14, " ")}</Text>{" "}
              <Text dimColor>
                {String(
                  (
                    pack.availability as Record<string, unknown> | undefined
                  )?.status ?? "?",
                )}{" "}
                · {String((pack.tools as unknown[])?.length ?? 0)} tools
              </Text>
            </Text>
          ))
        )
      ) : view === "audit" ? (
        audit === null ? (
          <Text dimColor>Loading audit...</Text>
        ) : audit.length === 0 ? (
          <Text dimColor>No audit entries</Text>
        ) : (
          audit.slice(0, 20).map((row) => (
            <Text key={String(row.audit_id)} wrap="truncate-end">
              <Text color="cyan">{String(row.actor ?? "-").padEnd(10, " ")}</Text>{" "}
              <Text color="yellow">
                {String(row.action ?? "-").padEnd(18, " ")}
              </Text>{" "}
              <Text dimColor>{String(row.resource ?? "").slice(0, 28)}</Text>
            </Text>
          ))
        )
      ) : view === "health" ? (
        health === null ? (
          <Text dimColor>Loading health...</Text>
        ) : Object.keys(health).length === 0 ? (
          <Text dimColor>No components</Text>
        ) : (
          Object.entries(health).map(([key, value]) => (
            <Text key={key} wrap="truncate-end">
              <Text color="cyan">{key.padEnd(14, " ")}</Text>{" "}
              <Text
                color={
                  String((value as Record<string, unknown>).status) === "ok"
                    ? "green"
                    : "red"
                }
              >
                {String(
                  (value as Record<string, unknown>).status ?? "?",
                ).padEnd(10, " ")}
              </Text>{" "}
              <Text dimColor>
                {String((value as Record<string, unknown>).detail ?? "")}
              </Text>
            </Text>
          ))
        )
      ) : view === "loops" ? (
        loopProfiles === null ? (
          <Text dimColor>Loading loop profiles...</Text>
        ) : loopProfiles.length === 0 ? (
          <Text dimColor>No loop profiles</Text>
        ) : (
          loopProfiles.map((profile) => (
            <Text key={String(profile.name)} wrap="truncate-end">
              <Text color="cyan">
                {String(profile.name).padEnd(22, " ")}
              </Text>{" "}
              <Text dimColor>
                v{String(profile.version)} {String(profile.category)} risk=
                {String(profile.risk_level)} sandbox={String(
                  profile.sandbox_profile,
                )}
              </Text>
              {" "}
              <Text dimColor wrap="truncate-end">
                {String(profile.oracle ?? "")} ·{" "}
                {String(profile.success_criteria ?? "").slice(0, 52)}
              </Text>
            </Text>
          ))
        )
      ) : view === "presets" ? (
        loopPresets === null ? (
          <Text dimColor>Loading loop presets...</Text>
        ) : loopPresets.length === 0 ? (
          <Text dimColor>No loop presets</Text>
        ) : (
          loopPresets.map((preset) => (
            <Text key={String(preset.preset_id)} wrap="truncate-end">
              <Text color="cyan">
                {String(preset.preset_id).padEnd(18, " ")}
              </Text>{" "}
              <Text color="green">{String(preset.label).padEnd(16, " ")}</Text>{" "}
              <Text dimColor wrap="truncate-end">
                {String(preset.description ?? "").slice(0, 68)}
              </Text>
            </Text>
          ))
        )
      ) : knowledge === null ? (
        <Text dimColor>加载知识库...</Text>
      ) : knowledge.length === 0 ? (
        <Text dimColor>知识库为空</Text>
      ) : (
        knowledge.slice(0, 30).map((chunk) => (
          <Text key={String(chunk.chunk_id)} wrap="truncate-end">
            <Text color="cyan">{String(chunk.chunk_id).padEnd(24, " ")}</Text>{" "}
            <Text dimColor>
              {String(chunk.source_ref)} · {String(chunk.trust)}
            </Text>
          </Text>
        ))
      )}
    </Box>
  );
}

export function App() {
  const { exit } = useApp();
  useEffect(() => {
    const onEnd = () => exit();
    if (process.stdin.isTTY) {
      process.stdin.on("end", onEnd);
    }
    return () => {
      if (process.stdin.isTTY) {
        process.stdin.off("end", onEnd);
      }
    };
  }, [exit]);
  const [runs, setRuns] = useState<RunState[]>([]);
  const [selected, setSelected] = useState(0);
  const [detail, setDetail] = useState<RunState | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [observations, setObservations] = useState<WebObservation[]>([]);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [evidence, setEvidence] = useState<Array<Record<string, unknown>>>([]);
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [exportedPath, setExportedPath] = useState<string | null>(null);
  const [view, setView] = useState<View>("events");
  const [home, setHome] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [diagnostics, setDiagnostics] = useState<Record<string, unknown> | null>(
    null,
  );
  const [graphMetrics, setGraphMetrics] = useState<
    Record<string, unknown> | null
  >(null);
  const [assets, setAssets] = useState<Record<string, unknown> | null>(null);
  const [memoryData, setMemoryData] = useState<Record<string, unknown> | null>(
    null,
  );
  const [help, setHelp] = useState(false);
  const [lastSync, setLastSync] = useState<string | null>(null);
  const [messageMode, setMessageMode] = useState(false);
  const [draftMessage, setDraftMessage] = useState("");
  const [messageHint, setMessageHint] = useState<string | null>(null);
  const [createStage, setCreateStage] = useState<
    null | "target" | "name" | "intent" | "args" | "confirm"
  >(null);
  const [createDraft, setCreateDraft] = useState("");
  const [createTarget, setCreateTarget] = useState("");
  const [createName, setCreateName] = useState("");
  const [createIntent, setCreateIntent] = useState("");
  const [createForcedArgs, setCreateForcedArgs] = useState("{}");
  const [createTemplate, setCreateTemplate] = useState<
    "default" | "code_audit"
  >("default");
  const [createError, setCreateError] = useState<string | null>(null);
  const [filterMode, setFilterMode] = useState<"all" | "active" | "done" | "failed">(
    "all",
  );
  const [commandMode, setCommandMode] = useState(false);
  const [commandDraft, setCommandDraft] = useState("");
  const [commandHistory, setCommandHistory] = useState<string[]>([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const [resourceView, setResourceView] = useState<
    | "providers"
    | "skills"
    | "mcp"
    | "nodes"
    | "acceptance"
    | "knowledge"
    | "memory"
    | "sessions"
    | "vulns"
    | "risk"
    | "tools"
    | "audit"
    | "health"
    | "loops"
    | "presets"
    | null
  >(null);
  const [knowledgeData, setKnowledgeData] = useState<
    Array<Record<string, unknown>> | null
  >(null);
  const [sessionsData, setSessionsData] = useState<
    Array<Record<string, unknown>> | null
  >(null);
  const [vulnsData, setVulnsData] = useState<
    Array<Record<string, unknown>> | null
  >(null);
  const [riskData, setRiskData] = useState<Record<string, unknown> | null>(null);
  const [toolsData, setToolsData] = useState<
    Array<Record<string, unknown>> | null
  >(null);
  const [auditData, setAuditData] = useState<
    Array<Record<string, unknown>> | null
  >(null);
  const [loopProfilesData, setLoopProfilesData] = useState<
    Array<Record<string, unknown>> | null
  >(null);
  const [loopPresetsData, setLoopPresetsData] = useState<
    Array<Record<string, unknown>> | null
  >(null);
  const [nodesData, setNodesData] = useState<
    Array<Record<string, unknown>> | null
  >(null);
  const [acceptanceData, setAcceptanceData] = useState<Record<
    string,
    unknown
  > | null>(null);

  useEffect(() => {
    let stopped = false;
    const tick = async () => {
      try {
        const next = await client.listRuns();
        const diagnosticData = await client.requestPublic("/api/v1/diagnostics");
        const [skillsData, mcpData] = await Promise.all([
          client.requestPublic("/api/v1/runtime/skills"),
          client.requestPublic("/api/v1/runtime/mcp"),
        ]);
        const [tools, audit, nodes, acceptance, loopProfiles, loopPresets] =
          await Promise.all([
          client.listToolPacks(),
          client.listAuditLogs({ limit: 50 }),
          client.requestPublic("/api/v1/remote/nodes"),
          client.requestPublic("/api/v1/acceptance"),
          client.listLoopProfiles(),
          client.listLoopPresets(),
        ]);
        const memoryPayload = await client.requestPublic(
          "/api/v1/memory?project_id=default&include_stale=true&limit=200",
        );
        if (!stopped) {
          setRuns(next);
          setDiagnostics(diagnosticData);
          setAssets({
            tools: diagnosticData.tools,
            skills: skillsData,
            mcp: mcpData,
            storage: diagnosticData.storage,
          });
          setMemoryData(memoryPayload);
          setToolsData(tools);
          setAuditData(audit);
          setLoopProfilesData(
            Object.values(
              (loopProfiles as Record<string, Record<string, unknown>>) ??
                {},
            ),
          );
          setLoopPresetsData(
            Object.values(
              (loopPresets as Record<string, Record<string, unknown>>) ??
                {},
            ),
          );
          setNodesData(nodes as unknown as Array<Record<string, unknown>>);
          setAcceptanceData(acceptance);
          setError(null);
          setLastSync(new Date().toISOString().slice(11, 19));
        }
        if (detail) {
          const run = await client.getRun(detail.run_id);
          const eventList = await client.getEvents(detail.run_id, 0);
          const observationList = await client.getWebObservations(detail.run_id);
          const findingList = await client.listFindings(detail.run_id);
          const evidenceData = await client.requestPublic(
            `/api/v1/runs/${detail.run_id}/evidence`,
          );
          const approvalList = await client.listApprovals(detail.run_id);
          const metricData = await client.requestPublic(
            `/api/v1/runs/${detail.run_id}/graph-metrics`,
          );
          if (!stopped) {
            setDetail(run);
            setEvents(eventList);
            setObservations(observationList);
            setFindings(findingList);
            setEvidence(
              evidenceData as unknown as Array<Record<string, unknown>>,
            );
            setApprovals(approvalList);
            setGraphMetrics(metricData);
          }
        }
      } catch (err) {
        if (!stopped) {
          setError(String(err));
        }
      }
    };
    void tick();
    const timer = setInterval(() => void tick(), 3000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [detail?.run_id]);

  const displayRuns = runs.filter((run) => {
    if (filterMode === "active") {
      return ["requested", "running", "claimed", "paused"].includes(run.status);
    }
    if (filterMode === "done") {
      return ["succeeded", "failed", "cancelled"].includes(run.status);
    }
    if (filterMode === "failed") {
      return ["failed", "cancelled"].includes(run.status);
    }
    return true;
  });

  const startNewMission = async () => {
    try {
      const project = await client.createProject(`tui-${Date.now()}`);
      const target = await client.createTarget(project.project_id, createTarget);
      let forcedToolArgs: Record<string, unknown> = {};
      try {
        forcedToolArgs = JSON.parse(createForcedArgs || "{}");
      } catch {
        forcedToolArgs = {};
      }
      const mission = await client.createMission(
        project.project_id,
        createName,
        createTemplate === "code_audit"
          ? {
              target_id: target.target_id,
              mission: createIntent,
              max_turns: 5,
              streaming: false,
              mode: "multi_role",
              role_template: "code_audit",
              required_categories: ["security", "HardcodedSecret"],
              min_severity: "low",
              code_tools: ["code.sast.semgrep", "code.secrets.detect"],
              scanner_tools: ["code.sast.semgrep", "code.secrets.detect"],
              allowed_tools: [
                "code.sast.semgrep",
                "code.secrets.detect",
                "run.finish",
              ],
              forced_tool_args: forcedToolArgs,
            }
          : {
          target_id: target.target_id,
          mission: createIntent,
          max_turns: 5,
          streaming: false,
          forced_tool_args: forcedToolArgs,
            },
      );
      const run = await client.startRun(
        mission.mission_id,
        crypto.randomUUID(),
      );
      setDetail(run);
      setEvents([]);
      setView("events");
      setCreateStage(null);
      setCreateTarget("");
      setCreateName("");
      setCreateIntent("");
      setCreateForcedArgs("{}");
      setCreateTemplate("default");
      setCreateError(null);
    } catch (err) {
      setCreateError(String(err));
    }
  };

  const executeCommand = async (raw: string) => {
    const trimmed = raw.trim();
    const [name, ...rest] = trimmed.split(/\s+/);
    switch (name) {
      case "/help":
        setHelp(true);
        break;
      case "/new":
        setCreateStage("target");
        setCreateDraft("");
        setCreateTemplate("default");
        setCreateError(null);
        break;
      case "/new-code":
        setCreateStage("target");
        setCreateDraft("");
        setCreateTemplate("code_audit");
        setCreateError(null);
        break;
      case "/runs":
        setDetail(null);
        setResourceView(null);
        break;
      case "/providers":
        setResourceView("providers");
        break;
      case "/skills":
        setResourceView("skills");
        break;
      case "/mcp":
        setResourceView("mcp");
        break;
      case "/nodes":
        setResourceView("nodes");
        break;
      case "/acceptance":
        setResourceView("acceptance");
        break;
      case "/knowledge":
        setResourceView("knowledge");
        void client
          .requestPublic("/api/v1/knowledge")
          .then((data) =>
            setKnowledgeData(
              data as unknown as Array<Record<string, unknown>>,
            ),
          )
          .catch((err: unknown) => setMessageHint(String(err)));
        break;
      case "/sessions":
        setResourceView("sessions");
        void client
          .requestPublic("/api/v1/sessions")
          .then((data) =>
            setSessionsData(
              data as unknown as Array<Record<string, unknown>>,
            ),
          )
          .catch((err: unknown) => setMessageHint(String(err)));
        break;
      case "/vulns":
        setResourceView("vulns");
        void client
          .requestPublic("/api/v1/vulnerabilities")
          .then((data) =>
            setVulnsData(
              data as unknown as Array<Record<string, unknown>>,
            ),
          )
          .catch((err: unknown) => setMessageHint(String(err)));
        break;
      case "/risk":
        setResourceView("risk");
        void client
          .requestPublic("/api/v1/risk")
          .then((data) => setRiskData(data))
          .catch((err: unknown) => setMessageHint(String(err)));
        break;
      case "/tools":
        setResourceView("tools");
        break;
      case "/audit":
        setResourceView("audit");
        break;
      case "/health":
        setResourceView("health");
        break;
      case "/loop-profiles":
        setResourceView("loops");
        break;
      case "/loop-presets":
        setResourceView("presets");
        break;
      case "/retrieval":
        try {
          const settings = await client.requestPublic(
            "/api/v1/settings/retrieval",
          );
          setMessageHint(
            JSON.stringify(settings ?? {}, null, 2).slice(0, 800),
          );
        } catch (err) {
          setMessageHint(String(err));
        }
        break;
      case "/retrieval-set": {
        const raw = rest.join(" ").trim();
        if (!raw) {
          setMessageHint(
            "用法：/retrieval-set <json>，例如 /retrieval-set {\"embedding\":{\"backend\":\"ollama\",\"endpoint\":\"http://127.0.0.1:11434/v1\",\"model\":\"nomic-embed-text\"}}",
          );
          break;
        }
        try {
          const payload = JSON.parse(raw);
          await client.requestJson(
            "POST",
            "/api/v1/settings/retrieval",
            payload,
          );
          setMessageHint("retrieval settings saved");
        } catch (err) {
          setMessageHint(String(err));
        }
        break;
      }
      case "/evidence":
        if (detail) {
          setView("evidence");
        } else {
          setMessageHint("请先打开一个运行，再查看证据。");
        }
        break;
      case "/findings":
        if (detail) {
          setView("findings");
        } else {
          setMessageHint("请先打开一个运行，再查看发现。");
        }
        break;
      case "/graph":
        if (detail) {
          setView("graph");
        } else {
          setMessageHint("请先打开一个运行，再查看图。");
        }
        break;
      case "/memory":
        setResourceView("memory");
        break;
      case "/memory-record": {
        const subject = rest[0];
        const predicate = rest[1];
        const value = rest.slice(2).join(" ");
        if (!subject || !predicate || !value) {
          setMessageHint(
            "用法：/memory-record <subject> <predicate> <value...>",
          );
          break;
        }
        try {
          await client.requestJson(
            "POST",
            "/api/v1/memory/record",
            {
              subject,
              predicate,
              value,
              trust: "user_approved",
            },
          );
          setMessageHint(`memory fact recorded: ${subject} ${predicate}`);
          setMemoryData(
            await client.requestPublic(
              "/api/v1/memory?project_id=default&include_stale=true&limit=200",
            ),
          );
        } catch (err) {
          setMessageHint(String(err));
        }
        break;
      }
      case "/memory-fix": {
        const subject = rest[0];
        const predicate = rest[1];
        const value = rest.slice(2).join(" ");
        if (!subject || !predicate || !value) {
          setMessageHint(
            "用法：/memory-fix <subject> <predicate> <value...>",
          );
          break;
        }
        try {
          await client.requestJson(
            "POST",
            "/api/v1/memory/fix",
            {
              subject,
              predicate,
              value,
              reason: "tui_operator_fix",
            },
          );
          setMessageHint(`memory fact fixed: ${subject} ${predicate}`);
          setMemoryData(
            await client.requestPublic(
              "/api/v1/memory?project_id=default&include_stale=true&limit=200",
            ),
          );
        } catch (err) {
          setMessageHint(String(err));
        }
        break;
      }
      case "/memory-clear": {
        try {
          const result = await client.requestJson(
            "POST",
            "/api/v1/memory/clear",
            { reason: "tui_operator_clear" },
          );
          setMessageHint(`memory cleared: ${JSON.stringify(result)}`);
          setMemoryData(
            await client.requestPublic(
              "/api/v1/memory?project_id=default&include_stale=true&limit=200",
            ),
          );
        } catch (err) {
          setMessageHint(String(err));
        }
        break;
      }
      case "/msg": {
        const text = rest.join(" ").trim();
        if (detail && detail.status === "paused") {
          if (!text) {
            setMessageHint("用法：/msg <指令内容>");
          } else {
            void client
              .sendMessage(detail.run_id, text, crypto.randomUUID(), "tui-operator")
              .catch((err: unknown) => setError(String(err)));
          }
        } else {
          setMessageHint("仅暂停状态下可发送指令，先按 p 暂停。");
        }
        break;
      }
      case "/pause":
      case "/resume":
      case "/cancel": {
        if (!detail) {
          setMessageHint("请先打开一个运行。");
        } else {
          const action = name.slice(1) as "pause" | "resume" | "cancel";
          void client
            .runCommand(detail.run_id, action, crypto.randomUUID())
            .catch((err: unknown) => setError(String(err)));
        }
        break;
      }
      case "/filter":
        setFilterMode((current) =>
          current === "all"
            ? "active"
            : current === "active"
              ? "done"
              : current === "done"
                ? "failed"
                : "all",
        );
        setSelected(0);
        break;
      case "/fork": {
        const runId = rest[0] ?? detail?.run_id;
        if (!runId) {
          setMessageHint("usage: /fork <run-id>");
          break;
        }
        try {
          const run = await client.forkRun(runId, crypto.randomUUID());
          setDetail(run);
          setMessageHint(`forked ${run.run_id}`);
        } catch (err) {
          setMessageHint(String(err));
        }
        break;
      }
      case "/takeover": {
        const runId = rest[0] ?? detail?.run_id;
        const takenBy = rest[1] ?? "tui-operator";
        if (!runId) {
          setMessageHint("usage: /takeover <run-id> [name]");
          break;
        }
        try {
          const run = await client.takeoverRun(
            runId,
            takenBy,
            crypto.randomUUID(),
            "tui takeover",
          );
          setDetail(run);
          setMessageHint(`takeover by ${takenBy}: ${run.run_id}`);
        } catch (err) {
          setMessageHint(String(err));
        }
        break;
      }
      case "/dispatch": {
        const nodeId = rest[0];
        const taskRef = rest[1] ?? `tui_${Date.now()}`;
        const command = rest.slice(2);
        if (!nodeId || command.length === 0) {
          setMessageHint("用法：/dispatch <node-id> [task-ref] <command...>");
          break;
        }
        try {
          const result = await client.requestJson<Record<string, unknown>>(
            "POST",
            `/api/v1/remote/nodes/${encodeURIComponent(nodeId)}/dispatch`,
            {
              task_ref: taskRef,
              payload: { command },
              lease_seconds: 600,
            },
          );
          const lease =
            (result.lease as Record<string, unknown> | undefined) ?? {};
          setMessageHint(
            `dispatched ${taskRef} -> ${nodeId} lease=${String(
              lease.lease_id ?? "",
            )}`,
          );
        } catch (err) {
          setMessageHint(String(err));
        }
        break;
      }
      case "/provider-add": {
        const [providerId, endpoint, model, apiKeyRef, reasoningEffort, retries] =
          rest;
        if (!providerId || !endpoint || !model) {
          setMessageHint(
            "用法：/provider-add <id> <endpoint> <model> [api-key-ref] [reasoning-effort] [retries]",
          );
          break;
        }
        try {
          await client.registerProvider({
            provider_id: providerId,
            endpoint,
            model,
            status: "ok",
            api_key_ref: apiKeyRef || undefined,
            reasoning_effort:
              reasoningEffort && reasoningEffort !== "none"
                ? reasoningEffort
                : undefined,
            retries: retries ? Number(retries) : undefined,
          });
          setMessageHint(`provider ${providerId} registered`);
        } catch (err) {
          setMessageHint(String(err));
        }
        break;
      }
      case "/provider-default": {
        const [providerId, endpoint, model, apiKeyRef] = rest;
        if (!providerId || !endpoint || !model) {
          setMessageHint(
            "用法：/provider-default <id> <endpoint> <model> [api-key-ref]",
          );
          break;
        }
        try {
          await client.setProviderDefault({
            provider_id: providerId,
            endpoint,
            model,
            api_key_ref: apiKeyRef || undefined,
          });
          setMessageHint(`default provider set to ${providerId}`);
        } catch (err) {
          setMessageHint(String(err));
        }
        break;
      }
      case "/provider-test": {
        const providerId = rest[0];
        if (!providerId) {
          setMessageHint("用法：/provider-test <provider-id>");
          break;
        }
        const providers =
          (diagnostics?.providers as
            | Array<Record<string, unknown>>
            | undefined) ?? [];
        const provider = providers.find(
          (item) => String(item.provider_id) === providerId,
        );
        if (!provider) {
          setMessageHint(`provider ${providerId} not found`);
          break;
        }
        try {
          const result = await client.probeProvider({
            provider_id: providerId,
            endpoint: String(provider.endpoint),
            model: String(provider.model),
            api_key_ref: provider.api_key_ref
              ? String(provider.api_key_ref)
              : undefined,
            timeout_seconds: 10,
          });
          setMessageHint(
            `provider ${providerId}: ${JSON.stringify(result).slice(0, 160)}`,
          );
        } catch (err) {
          setMessageHint(String(err));
        }
        break;
      }
      case "/mcp-add": {
        const serverId = rest[0];
        const name = rest[1];
        const kind = rest[2] ?? "local";
        const command = rest.slice(3).join(" ");
        if (!serverId || !name) {
          setMessageHint(
            "用法：/mcp-add <server-id> <name> [kind] [command...]",
          );
          break;
        }
        try {
          await client.registerMcp({
            server_id: serverId,
            name,
            status: "available",
            kind,
            command,
          });
          setMessageHint(`mcp ${serverId} registered`);
        } catch (err) {
          setMessageHint(String(err));
        }
        break;
      }
      case "/mcp-test": {
        const serverId = rest[0];
        if (!serverId) {
          setMessageHint("用法：/mcp-test <server-id>");
          break;
        }
        try {
          const result = await client.testMcp(serverId);
          setMessageHint(JSON.stringify(result).slice(0, 200));
        } catch (err) {
          setMessageHint(String(err));
        }
        break;
      }
      case "/skill-add": {
        const [skillRef, name, trigger, runner, riskLevel] = rest;
        if (!skillRef || !name) {
          setMessageHint(
            "用法：/skill-add <skill-ref> <name> [trigger] [runner] [risk-level]",
          );
          break;
        }
        try {
          await client.registerSkill({
            skill_ref: skillRef,
            name,
            version: "1",
            status: "available",
            trigger: trigger || "",
            runner: runner || "",
            risk_level: riskLevel || "L1",
          });
          setMessageHint(`skill ${skillRef} registered`);
        } catch (err) {
          setMessageHint(String(err));
        }
        break;
      }
      case "/skill-delete": {
        const skillRef = rest[0];
        if (!skillRef) {
          setMessageHint("用法：/skill-delete <skill-ref>");
          break;
        }
        try {
          await client.deleteSkill(skillRef);
          setMessageHint(`skill ${skillRef} deleted`);
        } catch (err) {
          setMessageHint(String(err));
        }
        break;
      }
      case "/knowledge-delete": {
        const chunkId = rest[0];
        if (!chunkId) {
          setMessageHint("用法：/knowledge-delete <chunk-id>");
          break;
        }
        try {
          await client.requestJson(
            "DELETE",
            `/api/v1/knowledge/${encodeURIComponent(chunkId)}`,
          );
          setMessageHint(`knowledge ${chunkId} deleted`);
        } catch (err) {
          setMessageHint(String(err));
        }
        break;
      }
      case "/knowledge-add": {
        const chunkId = rest[0];
        const sourceRef = rest[1];
        const content = rest.slice(2).join(" ");
        if (!chunkId || !sourceRef || !content) {
          setMessageHint(
            "用法：/knowledge-add <chunk-id> <source-ref> <content...>",
          );
          break;
        }
        try {
          await client.requestJson("POST", "/api/v1/knowledge", {
            chunk_id: chunkId,
            source_ref: sourceRef,
            content,
            trust: "project_trusted",
            version: "1",
            subjects: [],
          });
          setMessageHint(`knowledge ${chunkId} added`);
        } catch (err) {
          setMessageHint(String(err));
        }
        break;
      }
      case "/assets-list": {
        const projectId = rest[0] ?? "";
        try {
          const pathName = projectId
            ? `/api/v1/assets?project_id=${encodeURIComponent(projectId)}`
            : "/api/v1/assets";
          const rows = ((await client.requestPublic(pathName)) as
            | Record<string, unknown>
            | undefined) as unknown as
            | Array<Record<string, unknown>>
            | undefined;
          setMessageHint(
            `assets: ${rows?.length ?? 0} rows${
              projectId ? ` (project ${projectId})` : ""
            }`,
          );
        } catch (err) {
          setMessageHint(String(err));
        }
        break;
      }
      case "/asset-add": {
        const projectId = rest[0];
        const value = rest[1];
        const kind = rest[2] ?? "url";
        const status = rest[3] ?? "known";
        if (!projectId || !value) {
          setMessageHint(
            "用法：/asset-add <project-id> <value> [kind] [status]",
          );
          break;
        }
        try {
          await client.requestJson("POST", "/api/v1/assets", {
            project_id: projectId,
            kind,
            value,
            source: "tui",
            status,
          });
          setMessageHint(`asset ${value} added`);
        } catch (err) {
          setMessageHint(String(err));
        }
        break;
      }
      case "/asset-update": {
        const assetId = rest[0];
        const status = rest[1];
        if (!assetId || !status) {
          setMessageHint("用法：/asset-update <asset-id> <status>");
          break;
        }
        try {
          await client.requestJson(
            "PATCH",
            `/api/v1/assets/${encodeURIComponent(assetId)}`,
            { status },
          );
          setMessageHint(`asset ${assetId} -> ${status}`);
        } catch (err) {
          setMessageHint(String(err));
        }
        break;
      }
      case "/asset-delete": {
        const assetId = rest[0];
        if (!assetId) {
          setMessageHint("用法：/asset-delete <asset-id>");
          break;
        }
        try {
          await client.requestJson(
            "DELETE",
            `/api/v1/assets/${encodeURIComponent(assetId)}`,
          );
          setMessageHint(`asset ${assetId} deleted`);
        } catch (err) {
          setMessageHint(String(err));
        }
        break;
      }
      case "/report": {
        const runId = rest[0];
        const format = rest[1] ?? "bundle";
        if (!runId) {
          setMessageHint("用法：/report <run-id> [bundle|markdown|html]");
          break;
        }
        try {
          if (format === "markdown" || format === "html") {
            const extension = format === "html" ? "html" : "md";
            const dir = path.join(process.cwd(), "reports");
            mkdirSync(dir, { recursive: true });
            const suffix =
              format === "html" ? "/report.html" : "/report";
            const response = await fetch(
              `${baseUrl}/api/v1/runs/${encodeURIComponent(runId)}${suffix}`,
            );
            if (!response.ok) {
              throw new Error(`HTTP ${response.status}`);
            }
            const target = path.join(
              dir,
              `report-${runId}.${extension}`,
            );
            writeFileSync(target, await response.text(), "utf-8");
            setMessageHint(`report saved: ${target}`);
          } else {
            const outPath = await exportReportBundle(runId, baseUrl);
            setMessageHint(`report saved: ${outPath}`);
          }
        } catch (err) {
          setMessageHint(String(err));
        }
        break;
      }
      case "/exit":
        exit();
        break;
      default:
        setMessageHint(`未知命令 ${name ?? "/"}，输入 /help 查看可用命令。`);
    }
  };

  useInput((input, key) => {
    if (home) {
      if (input === "q" && !key.ctrl) {
        exit();
        return;
      }
      if (input === "/") {
        setHome(false);
        setCommandMode(true);
        setCommandDraft("/");
        setHistoryIndex(-1);
        return;
      }
      setHome(false);
      return;
    }
    if (help) {
      if (input === "q" || input === "?") {
        setHelp(false);
      }
      return;
    }
    if (input.length > 1) {
      const batch = input.replace(/\r?\n/g, "\r");
      if (commandMode) {
        const nextDraft = commandDraft + batch;
        const hasReturn = batch.includes("\r");
        const finalDraft = hasReturn
          ? nextDraft.replace(/\r$/, "")
          : nextDraft;
        setCommandDraft(finalDraft);
        if (hasReturn && finalDraft.trim()) {
          void executeCommand(finalDraft);
          setCommandDraft("");
          setCommandMode(false);
          setHistoryIndex(-1);
        }
        return;
      }
      if (batch.startsWith("/")) {
        setCommandMode(true);
        setCommandDraft(batch);
        const hasReturn = batch.includes("\r");
        const finalDraft = hasReturn ? batch.replace(/\r$/, "") : batch;
        if (hasReturn && finalDraft.trim()) {
          void executeCommand(finalDraft);
          setCommandDraft("");
          setCommandMode(false);
          setHistoryIndex(-1);
        }
        return;
      }
      return;
    }
    if (input === "?") {
      setHelp(true);
      return;
    }
    if (commandMode) {
      if (key.escape) {
        setCommandMode(false);
        setCommandDraft("");
        setHistoryIndex(-1);
      } else if (key.tab) {
        const completion = completeSlashCommand(commandDraft);
        if (completion.draft) {
          setCommandDraft(completion.draft);
        } else if (completion.hint) {
          setMessageHint(completion.hint);
        }
      } else if (key.upArrow) {
        const next = nextHistoryIndex(
          historyIndex,
          -1,
          commandHistory.length,
        );
        setHistoryIndex(next);
        if (next >= 0) {
          setCommandDraft(commandHistory[next]);
        }
      } else if (key.downArrow) {
        const next = nextHistoryIndex(
          historyIndex,
          1,
          commandHistory.length,
        );
        setHistoryIndex(next);
        setCommandDraft(next >= 0 ? commandHistory[next] : "/");
      } else if (key.backspace) {
        setCommandDraft((current) => current.slice(0, -1));
      } else if (input === "\r") {
        const trimmed = commandDraft.trim();
        if (trimmed) {
          setCommandHistory((current) => {
            const withoutDuplicate = current.filter(
              (item) => item !== trimmed,
            );
            return [...withoutDuplicate, trimmed].slice(-50);
          });
        }
        void executeCommand(commandDraft);
        setCommandDraft("");
        setCommandMode(false);
        setHistoryIndex(-1);
      } else if (input.length === 1) {
        setCommandDraft((current) => current + input);
      }
      return;
    }
    if (resourceView && input === "q") {
      setResourceView(null);
      return;
    }
    if (messageMode) {
      if (key.escape) {
        setMessageMode(false);
        setDraftMessage("");
      } else if (key.backspace) {
        setDraftMessage((current) => current.slice(0, -1));
      } else if (input === "\r") {
        const text = draftMessage.trim();
        if (text && detail) {
          void client
            .sendMessage(detail.run_id, text, crypto.randomUUID(), "tui-operator")
            .then(() => {
              setDraftMessage("");
              setMessageMode(false);
            })
            .catch((err: unknown) => setError(String(err)));
        }
      } else if (input.length === 1) {
        setDraftMessage((current) => current + input);
      }
      return;
    }
    if (createStage === "confirm") {
      if (input === "y") {
        void startNewMission();
      } else if (input === "n" || input === "q") {
        setCreateStage(null);
        setCreateError(null);
      }
      return;
    }
    if (
      createStage === "target" ||
      createStage === "name" ||
      createStage === "intent" ||
      createStage === "args"
    ) {
      if (key.escape) {
        setCreateStage(null);
        setCreateDraft("");
        setCreateError(null);
      } else if (key.backspace) {
        setCreateDraft((current) => current.slice(0, -1));
      } else if (input === "\r") {
        const value = createDraft.trim();
        if (!value) {
          setCreateError("输入不能为空");
          return;
        }
        if (createStage === "target") {
          setCreateTarget(value);
          setCreateStage("name");
        } else if (createStage === "name") {
          setCreateName(value);
          setCreateStage("intent");
        } else if (createStage === "intent") {
          setCreateIntent(value);
          setCreateStage("args");
        } else {
          setCreateForcedArgs(value || "{}");
          setCreateStage("confirm");
        }
        setCreateDraft("");
        setCreateError(null);
      } else if (input.length === 1) {
        setCreateDraft((current) => current + input);
      }
      return;
    }
    if (detail) {
      const viewByKey = VIEWS.find((item) => item.key === input);
      if (viewByKey) {
        setView(viewByKey.id);
      } else if (key.tab || key.rightArrow) {
        const index = VIEWS.findIndex((item) => item.id === view);
        setView(VIEWS[(index + 1) % VIEWS.length].id);
      } else if (key.leftArrow) {
        const index = VIEWS.findIndex((item) => item.id === view);
        setView(VIEWS[(index - 1 + VIEWS.length) % VIEWS.length].id);
      } else if (input === "q") {
        setDetail(null);
      } else if (input === "/") {
        setCommandMode(true);
        setCommandDraft("/");
        setHistoryIndex(-1);
      } else if (input === "m") {
        if (detail.status === "paused") {
          setMessageMode(true);
          setDraftMessage("");
          setMessageHint(null);
        } else {
          setMessageHint("仅暂停状态下可发送指令，先按 p 暂停。");
        }
      } else if (input === "E") {
        void exportReportBundle(detail.run_id, baseUrl)
          .then((outPath) => setExportedPath(outPath))
          .catch((err: unknown) => setError(String(err)));
      } else if (input === "a") {
        const pending = firstPendingApproval(approvals);
        if (pending) {
          void client
            .decideApproval(pending.approval_id, true, "tui-operator")
            .catch((err: unknown) => setError(String(err)));
        }
      } else if (input === "x") {
        const pending = firstPendingApproval(approvals);
        if (pending) {
          void client
            .decideApproval(pending.approval_id, false, "tui-operator")
            .catch((err: unknown) => setError(String(err)));
        }
      } else if (input === "F") {
        void client
          .forkRun(detail.run_id, crypto.randomUUID())
          .catch((err: unknown) => setError(String(err)));
      } else if (input === "T") {
        void client
          .takeoverRun(detail.run_id, "tui-operator", crypto.randomUUID())
          .catch((err: unknown) => setError(String(err)));
      } else if (input === "p") {
        void client
          .runCommand(detail.run_id, "pause", crypto.randomUUID())
          .catch((err: unknown) => setError(String(err)));
      } else if (input === "r") {
        void client
          .runCommand(detail.run_id, "resume", crypto.randomUUID())
          .catch((err: unknown) => setError(String(err)));
      } else if (input === "c") {
        void client
          .runCommand(detail.run_id, "cancel", crypto.randomUUID())
          .catch((err: unknown) => setError(String(err)));
      }
      return;
    }
    if (input === "q") {
      exit();
    } else if (input === "n") {
      setCreateStage("target");
      setCreateDraft("");
      setCreateError(null);
    } else if (input === "/") {
      setCommandMode(true);
      setCommandDraft("/");
      setHistoryIndex(-1);
    } else if (input === "f") {
      setFilterMode((current) =>
        current === "all"
          ? "active"
          : current === "active"
            ? "done"
            : current === "done"
              ? "failed"
              : "all",
      );
      setSelected(0);
    } else if (input === "\r") {
      const run = displayRuns[selected];
      if (run) {
        setDetail(run);
        setEvents([]);
        setObservations([]);
        setFindings([]);
        setEvidence([]);
        setApprovals([]);
        setGraphMetrics(null);
        setExportedPath(null);
        setView("events");
      }
    } else if (input === "j" && selected < displayRuns.length - 1) {
      setSelected(selected + 1);
    } else if (input === "k" && selected > 0) {
      setSelected(selected - 1);
    }
  });

  const activeCount = runs.filter((run) =>
    ["requested", "running", "claimed", "paused"].includes(run.status),
  ).length;

  if (home) {
    return (
      <Box flexDirection="column" padding={1} width="100%">
        <HomeScreen
          diagnostics={diagnostics}
          assets={assets}
          runs={runs}
        />
      </Box>
    );
  }

  return (
    <Box flexDirection="column" padding={1} width="100%">
      <Box
        borderStyle="double"
        borderColor="cyan"
        flexDirection="row"
        justifyContent="space-between"
        paddingX={1}
      >
        <Text bold color="cyan">
          VERIDIX AGENT
        </Text>
        <Text>
          {activeCount > 0 ? (
            <Text>
              <Spinner />{" "}
            </Text>
          ) : null}
          <Text color={activeCount > 0 ? "cyan" : "green"} bold>
            {activeCount > 0 ? `${activeCount} 运行中` : "空闲"}
          </Text>
          <Text dimColor>
            {" "}
            · {detail ? "运行详情" : "运行列表"} · {baseUrl}
          </Text>
          {lastSync ? <Text dimColor> · sync {lastSync}</Text> : null}
        </Text>
      </Box>
      {error ? (
        <Box marginTop={1}>
          <Text color="red">! {error}</Text>
        </Box>
      ) : null}
      {messageHint ? (
        <Box marginTop={1}>
          <Text color="yellow">! {messageHint}</Text>
        </Box>
      ) : null}
      {messageMode && detail ? (
        <Box marginTop={1}>
          <Text>
            <Text bold color="cyan">
              &gt; 指令:
            </Text>{" "}
            <Text>{draftMessage}</Text>
            <Text dimColor>  (Enter 发送 / Esc 取消)</Text>
          </Text>
        </Box>
      ) : null}
      {createStage && createStage !== "confirm" ? (
        <Box marginTop={1} flexDirection="column">
          <Text>
            <Text bold color="cyan">
              {createStage === "target"
                ? "> 目标 URL:"
                : createStage === "name"
                  ? "> 任务名称:"
                  : createStage === "intent"
                    ? "> 任务意图:"
                    : "> Forced tool args (JSON, 可留空):"}{" "}
            </Text>
            <Text>{createDraft}</Text>
            <Text dimColor>  (Enter 确认 / Esc 取消)</Text>
          </Text>
          {createError ? <Text color="red">! {createError}</Text> : null}
        </Box>
      ) : null}
      {createStage === "confirm" ? (
        <Box
          marginTop={1}
          borderStyle="single"
          borderColor="cyan"
          flexDirection="column"
          paddingX={1}
        >
          <Text bold color="cyan">
            确认创建任务
          </Text>
          <Text dimColor>目标: {createTarget}</Text>
          <Text dimColor>名称: {createName}</Text>
          <Text dimColor>意图: {createIntent}</Text>
          <Text dimColor>模板: {createTemplate}</Text>
          <Text dimColor>Forced args: {createForcedArgs || "{}"}</Text>
          {createError ? <Text color="red">! {createError}</Text> : null}
          <Text color="yellow">y=创建并启动  n=取消</Text>
        </Box>
      ) : null}
      {commandMode ? (
        <Box marginTop={1}>
          <Text>
            <Text bold color="cyan">
              &gt; /
            </Text>
            <Text>{commandDraft.slice(1)}</Text>
            <Text dimColor>  (Enter 执行 / Esc 取消)</Text>
          </Text>
        </Box>
      ) : null}
      <Box flexDirection="row" marginTop={1}>
        <Box width={46} flexShrink={0}>
          <RunList runs={displayRuns} selected={selected} />
        </Box>
        <Box flexGrow={1} paddingLeft={1}>
          {resourceView ? (
            <ResourceView
              view={resourceView}
              diagnostics={diagnostics}
              assets={assets}
              knowledge={knowledgeData}
              memoryData={memoryData}
              sessions={sessionsData}
              vulns={vulnsData}
              risk={riskData}
              tools={toolsData}
              audit={auditData}
              loopProfiles={loopProfilesData}
              loopPresets={loopPresetsData}
              nodes={nodesData}
              acceptance={acceptanceData}
              health={
                (diagnostics?.components as
                  | Record<string, Record<string, unknown>>
                  | undefined) ?? null
              }
            />
          ) : help ? (
            <Box
              borderStyle="single"
              borderColor="cyan"
              flexDirection="column"
              paddingX={1}
              width="100%"
            >
              <Text bold color="cyan">
                帮助
              </Text>
              <Text dimColor>
                j/k 选择运行，Enter 打开；n 新建任务，f 过滤；q 返回列表或退出。
              </Text>
              <Text dimColor>
                视图键：e 活动  G 图  M 记忆  o Web  f 发现  g 证据  d 审批  A 资产。
              </Text>
              <Text dimColor>
                p/r/c 暂停/恢复/取消；m 向暂停运行发送指令；F/T fork/takeover；E 导出报告；a/x 审批待办。
              </Text>
              <Text dimColor>
                / 斜杠命令：/new /new-code /runs /providers /provider-add /provider-default /provider-test /skills /skill-add /skill-delete /mcp /mcp-add /mcp-test /nodes /dispatch /acceptance /tools /audit /health /loop-profiles /loop-presets /retrieval /retrieval-set /knowledge /knowledge-add /knowledge-delete /memory /memory-record /memory-fix /memory-clear /assets-list /asset-add /asset-update /asset-delete /report /sessions /vulns /risk /msg /pause /resume /cancel /filter /exit。
              </Text>
              <Text dimColor>按 q 或 ? 关闭帮助。</Text>
            </Box>
          ) : detail ? (
            <RunDetail
              run={detail}
              events={events}
              observations={observations}
              findings={findings}
              evidence={evidence}
              approvals={approvals}
              exportedPath={exportedPath}
              graphMetrics={graphMetrics}
              assets={assets}
              memoryData={memoryData}
              view={view}
            />
          ) : (
            <Overview diagnostics={diagnostics} assets={assets} runs={runs} />
          )}
        </Box>
      </Box>
      <Footer detail={Boolean(detail)} filter={filterMode} />
    </Box>
  );
}

const isSeaLegacyEntry =
  process.env.VERIDIX_TUI_SEA === "1" ||
  (process.argv[1] ?? "").toLowerCase().includes("veridix-tui");
if (
  (process.argv[1] &&
    fileURLToPath(import.meta.url) === process.argv[1]) ||
  isSeaLegacyEntry
) {
  render(<App />);
}
