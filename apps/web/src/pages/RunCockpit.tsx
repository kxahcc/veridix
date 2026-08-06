import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import type { WheelEvent as ReactWheelEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  Activity,
  ArrowRightLeft,
  BrainCircuit,
  CheckCircle2,
  CirclePause,
  CirclePlay,
  CircleStop,
  Clock,
  Copy,
  FileText,
  FileSearch,
  GitFork,
  Globe,
  Hand,
  Layers,
  ListTree,
  Radar,
  RefreshCw,
  Search,
  Send,
  Server,
  ShieldCheck,
  Sparkles,
  TerminalSquare,
  User,
  Wrench,
  XCircle,
  Zap,
} from "lucide-react";
import { control, CONTROL_URL } from "../api.js";
import { ErrorBanner, Loading } from "../components/Status.js";
import { useRunSelection } from "../store.js";
import {
  Badge,
  EmptyState,
  Kpi,
  Notice,
  Panel,
  SyncStamp,
} from "../components/ui.js";
import { RunPicker } from "../components/RunPicker.js";
import type { AgentEvent } from "@veridix/sdk-typescript";
import {
  type GraphEdgeInput,
  type GraphNodeInput,
  layoutGraph,
} from "../graph-layout.js";

type MemoryFactRow = {
  fact_id: string;
  subject: string;
  predicate: string;
  value: string;
  status: string;
  expires_at?: string;
};

type MemoryProjection = {
  digest?: string;
  snapshot?: {
    active: number;
    conflict: number;
    stale: number;
    total_facts: number;
  };
  facts?: MemoryFactRow[];
};

const WORKFLOW_PHASES = [
  { id: "recon", label: "侦察与资产收集", desc: "域名 / 端口 / 资产探测", icon: Radar },
  { id: "web", label: "Web 探测", desc: "路径 / 指纹 / 接口发现", icon: Globe },
  { id: "verify", label: "漏洞验证", desc: "漏洞确认与证据固化", icon: ShieldCheck },
  { id: "exploit", label: "利用与复现", desc: "验证可利用性并复现", icon: Zap },
  { id: "report", label: "报告与归档", desc: "整理发现与交付报告", icon: FileText },
] as const;

type PhaseId = (typeof WORKFLOW_PHASES)[number]["id"];

function phaseForEvent(event: AgentEvent): PhaseId | null {
  const type = event.event_type;
  const payload = event.payload as Record<string, unknown>;
  const tool = String(payload.tool ?? payload.tool_ref ?? "").toLowerCase();
  const role = String(
    payload.node_id ?? payload.from_node ?? payload.to_node ?? "",
  ).toLowerCase();
  const nodeType = String(payload.node_type ?? "").toLowerCase();
  if (
    /probe|nmap|subfinder|amass|masscan|fofa|zoomeye|quake|shodan|host|recon|asset|dns/.test(
      `${tool} ${role} ${nodeType}`,
    )
  ) {
    return "recon";
  }
  if (
    /browser|nikto|dirb|gobuster|ffuf|httpx|wafw00f|web|discovery|fuzz|ferox/.test(
      `${tool} ${role} ${nodeType}`,
    ) ||
    /web_discovery/.test(`${role} ${nodeType}`)
  ) {
    return "web";
  }
  if (
    /sqlmap|nuclei|dalfox|xsser|verify|verifier|retest|wpscan|graphql|ssrf|sqli/.test(
      `${tool} ${role} ${nodeType}`,
    ) ||
    /verifier/.test(`${role} ${nodeType}`)
  ) {
    return "verify";
  }
  if (
    /metasploit|msf|exploit|hashcat|john|hydra|burp|c2|webshell/.test(
      `${tool} ${role} ${nodeType}`,
    )
  ) {
    return "exploit";
  }
  if (
    /report|finding|evidence/.test(`${type} ${tool} ${role}`) ||
    /reporter/.test(`${role} ${nodeType}`)
  ) {
    return "report";
  }
  return null;
}

function summarizePhases(events: AgentEvent[], runStatus: string) {
  const counts = new Map<PhaseId, number>();
  let maxIndex = -1;
  for (const event of events) {
    const phase = phaseForEvent(event);
    if (!phase) {
      continue;
    }
    counts.set(phase, (counts.get(phase) ?? 0) + 1);
    const index = WORKFLOW_PHASES.findIndex((item) => item.id === phase);
    maxIndex = Math.max(maxIndex, index);
  }
  const terminal = ["succeeded", "failed", "cancelled"].includes(runStatus);
  return WORKFLOW_PHASES.map((phase, index) => {
    const count = counts.get(phase.id) ?? 0;
    let state: "done" | "active" | "waiting" | "pending";
    if (count > 0) {
      state =
        terminal || index < maxIndex
          ? "done"
          : index === maxIndex
            ? runStatus === "paused"
              ? "waiting"
              : "active"
            : "pending";
    } else if (terminal || index < maxIndex) {
      state = "pending";
    } else {
      state = index === maxIndex + 1 && !terminal ? "active" : "pending";
    }
    return { ...phase, count, state };
  });
}

function GraphCanvas({
  nodes,
  edges,
}: {
  nodes: GraphNodeInput[];
  edges: GraphEdgeInput[];
}) {
  const width = 900;
  const height = 280;
  const [transform, setTransform] = useState({ x: 0, y: 0, k: 1 });
  const dragRef = useRef<{
    startX: number;
    startY: number;
    originX: number;
    originY: number;
  } | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const layout = layoutGraph(nodes, edges, width, height);

  const resetView = () => setTransform({ x: 0, y: 0, k: 1 });
  const onPointerDown = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (event.button !== 0) {
      return;
    }
    dragRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      originX: transform.x,
      originY: transform.y,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onPointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    if (!drag) {
      return;
    }
    setTransform((current) => ({
      ...current,
      x: drag.originX + (event.clientX - drag.startX),
      y: drag.originY + (event.clientY - drag.startY),
    }));
  };
  const onPointerUp = () => {
    dragRef.current = null;
  };
  const onWheel = (event: ReactWheelEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (!svg) {
      return;
    }
    const rect = svg.getBoundingClientRect();
    const px = event.clientX - rect.left;
    const py = event.clientY - rect.top;
    const factor = event.deltaY < 0 ? 1.12 : 0.9;
    setTransform((current) => {
      const k = Math.min(2.5, Math.max(0.5, current.k * factor));
      const x = px - ((px - current.x) * k) / current.k;
      const y = py - ((py - current.y) * k) / current.k;
      return { x, y, k };
    });
  };

  return (
    <svg
      className="graph-canvas"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="interactive graph structure"
      ref={svgRef}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onWheel={onWheel}
      onDoubleClick={resetView}
    >
      <defs>
        <marker
          id="arrow"
          markerWidth="10"
          markerHeight="10"
          refX="9"
          refY="3"
          orient="auto"
        >
          <path d="M0,0 L0,6 L9,3 z" fill="#5b7184" />
        </marker>
      </defs>
      <g transform={`translate(${transform.x} ${transform.y}) scale(${transform.k})`}>
        {layout.edges.map((edge, index) => (
          <g key={index}>
            <path
              d={edge.path}
              fill="none"
              stroke="#5b7184"
              strokeWidth="1.8"
              markerEnd="url(#arrow)"
            />
            {edge.edge.label && (
              <text
                x={(edge.from.x + edge.to.x) / 2}
                y={(edge.from.y + edge.to.y) / 2 - 30}
                textAnchor="middle"
                fontSize="10"
                fill="#7f93a6"
                paintOrder="stroke"
                stroke="#0e141b"
                strokeWidth="4"
              >
                {edge.edge.label}
              </text>
            )}
          </g>
        ))}
        {layout.nodes.map((item) => {
          const node = item.node;
          const widthPx = Math.max(104, node.id.length * 7.6 + 28);
          const fill =
            node.status === "succeeded"
              ? "rgba(52, 211, 153, 0.14)"
              : node.status === "failed"
                ? "rgba(248, 113, 113, 0.15)"
                : "rgba(251, 191, 36, 0.14)";
          const stroke =
            node.status === "succeeded"
              ? "#34d399"
              : node.status === "failed"
                ? "#f87171"
                : "#fbbf24";
          return (
            <g
              key={node.id}
              transform={`translate(${item.point.x} ${item.point.y})`}
              className="graph-node"
            >
              <rect
                x={-widthPx / 2}
                y={-38}
                width={widthPx}
                height={72}
                rx={9}
                fill={fill}
                stroke={stroke}
                strokeWidth="1.4"
                style={{ filter: `drop-shadow(0 0 8px ${stroke}55)` }}
              />
              <text
                x={0}
                y={-18}
                textAnchor="middle"
                fontSize="12"
                fontWeight="650"
                fill="#e8eef4"
              >
                {node.id}
              </text>
              <text
                x={0}
                y={2}
                textAnchor="middle"
                fontSize="10"
                fill="#8ea3b5"
              >
                {node.status}
              </text>
              {node.detail && (
                <text
                  x={0}
                  y={18}
                  textAnchor="middle"
                  fontSize="9"
                  fill="#7f93a6"
                >
                  {node.detail}
                </text>
              )}
            </g>
          );
        })}
      </g>
    </svg>
  );
}

type ActivityItem = {
  id: string;
  event: AgentEvent;
  dot: "ok" | "info" | "warn" | "danger" | "violet";
  icon: typeof Activity;
  title: string;
  detail?: ReactNode;
};

function iconFor(type: string): { icon: typeof Activity; dot: ActivityItem["dot"] } {
  if (type.startsWith("tool.")) {
    if (type.includes("completed")) {
      return { icon: CheckCircle2, dot: "ok" };
    }
    if (type.includes("failed")) {
      return { icon: XCircle, dot: "danger" };
    }
    if (type.includes("proposed")) {
      return { icon: Wrench, dot: "warn" };
    }
    return { icon: TerminalSquare, dot: "info" };
  }
  if (type.startsWith("model.")) {
    return { icon: Sparkles, dot: "violet" };
  }
  if (type.startsWith("graph.")) {
    if (type.includes("handoff")) {
      return { icon: ArrowRightLeft, dot: "violet" };
    }
    return { icon: ListTree, dot: "violet" };
  }
  if (type.startsWith("context.")) {
    return { icon: Layers, dot: "info" };
  }
  if (type.startsWith("run.")) {
    if (type.includes("queued") || type.includes("submitted")) {
      return { icon: Clock, dot: "warn" };
    }
    if (type.includes("paused")) {
      return { icon: CirclePause, dot: "warn" };
    }
    if (type.includes("cancelled") || type.includes("failed")) {
      return { icon: XCircle, dot: "danger" };
    }
    return { icon: Activity, dot: "ok" };
  }
  if (type.includes("approval")) {
    return { icon: ShieldCheck, dot: "danger" };
  }
  if (type.includes("finding")) {
    return { icon: FileSearch, dot: "danger" };
  }
  return { icon: Activity, dot: "info" };
}

function toolCard(event: AgentEvent): ReactNode {
  const payload = event.payload as Record<string, unknown>;
  const tool = String(payload.tool ?? payload.tool_ref ?? "tool");
  const args = payload.arguments ?? payload.args;
  const stdout = payload.stdout ?? payload.output ?? payload.result;
  const stderr = payload.stderr;
  const exitCode = payload.exit_code;
  const actionId = payload.action_id;
  const artifactRefs = payload.artifact_refs;
  return (
    <div className="tool-card">
      <div className="tool-card-head">
        <code>{tool}</code>
        {exitCode !== undefined && exitCode !== null ? (
          <span className="muted">exit {String(exitCode)}</span>
        ) : null}
        {actionId ? <span className="muted">{String(actionId)}</span> : null}
        {artifactRefs ? (
          <span className="muted">{(artifactRefs as unknown[]).length} artifacts</span>
        ) : null}
      </div>
      <div className="tool-card-body">
        {args !== undefined ? (
          <pre>{JSON.stringify(args, null, 2)}</pre>
        ) : null}
        {stdout !== undefined && stdout !== null ? (
          <pre>{String(stdout)}</pre>
        ) : null}
        {stderr !== undefined && stderr !== null ? (
          <pre style={{ color: "#f87171" }}>{String(stderr)}</pre>
        ) : null}
      </div>
    </div>
  );
}

function buildActivity(events: AgentEvent[]): ActivityItem[] {
  return events.map((event) => {
    const payload = event.payload as Record<string, unknown>;
    const type = event.event_type;
    const { icon, dot } = iconFor(type);
    let title = type;
    let detail: ReactNode;
    switch (type) {
      case "run.queued":
        title = "运行已排队";
        detail = <code>{String(payload.mission_id ?? "")}</code>;
        break;
      case "run.claimed":
        title = "Worker 接管";
        detail = <code>{String(payload.worker_id ?? "")}</code>;
        break;
      case "run.started":
        title = "运行启动";
        detail = <code>{String(payload.behavior_snapshot ?? "")}</code>;
        break;
      case "run.submitted":
        title = "任务已提交";
        detail = <code>{String(payload.user_input ?? "")}</code>;
        break;
      case "run.paused":
        title = "运行已暂停";
        detail = <code>{String(payload.reason ?? "")}</code>;
        break;
      case "run.resumed":
        title = "运行已恢复";
        break;
      case "run.cancelled":
        title = "运行已取消";
        detail = <code>{String(payload.reason ?? "")}</code>;
        break;
      case "run.finished":
        title = "运行结束";
        detail = <code>{String(payload.outcome ?? "")}</code>;
        break;
      case "model.turn.started":
        title = `模型轮次 ${String(payload.turn ?? "?")}`;
        detail = payload.reasoning_content ? (
          <span>{String(payload.reasoning_content).slice(0, 220)}</span>
        ) : undefined;
        break;
      case "model.turn.completed":
        title = `模型轮次完成 ${String(payload.turn ?? "?")}`;
        detail = payload.tokens ? (
          <code>{JSON.stringify(payload.tokens)}</code>
        ) : undefined;
        break;
      case "context.projection":
        title = "上下文投影";
        {
          const knowledge = payload.knowledge as
            | { included?: unknown[] }
            | undefined;
          const skills = payload.skills as
            | { included?: unknown[] }
            | undefined;
          const mcp = payload.mcp as { included?: unknown[] } | undefined;
          const memory = payload.memory as
            | { snapshot?: { total_facts?: number } }
            | undefined;
          detail = (
            <code>
              知识 {knowledge?.included?.length ?? 0} / 技能{" "}
              {skills?.included?.length ?? 0} / MCP {mcp?.included?.length ?? 0} / 记忆{" "}
              {memory?.snapshot?.total_facts ?? 0}
            </code>
          );
        }
        break;
      case "context.assembly":
        title = "上下文组装";
        detail = (
          <code>
            知识块 {String(payload.knowledge_blocks ?? 0)} · 记忆块{" "}
            {String(payload.memory_blocks ?? 0)} · 技能块{" "}
            {String(payload.skill_blocks ?? 0)} · MCP 块{" "}
            {String(payload.mcp_blocks ?? 0)}
          </code>
        );
        break;
      case "tool.proposed":
        title = `提议工具 ${String(payload.tool ?? "")}`;
        detail = toolCard(event);
        break;
      case "tool.authorized":
        title = `工具已授权 ${String(payload.tool ?? "")}`;
        detail = <code>rule={String(payload.rule ?? "")}</code>;
        break;
      case "tool.started":
        title = `工具启动 ${String(payload.tool ?? "")}`;
        detail = toolCard(event);
        break;
      case "tool.completed":
        title = `工具完成 ${String(payload.tool ?? "")}`;
        detail = toolCard(event);
        break;
      case "tool.failed":
        title = `工具失败 ${String(payload.tool ?? "")}`;
        detail = (
          <>
            <code>{String(payload.error ?? "")}</code>
            {payload.stderr ? (
              <pre style={{ color: "#f87171" }}>
                {String(payload.stderr)}
              </pre>
            ) : null}
            {payload.exit_code !== undefined &&
            payload.exit_code !== null ? (
              <span className="muted">
                exit {String(payload.exit_code)}
              </span>
            ) : null}
          </>
        );
        break;
      case "observation.ingested":
        title = "观测已摄入";
        detail = (
          <>
            <code>
              {String(payload.tool ?? "")} · parsed{" "}
              {(payload.parsed_observations as unknown[] | undefined)?.length ?? 0}
            </code>
          </>
        );
        break;
      case "graph.started":
        title = "图编排启动";
        detail = (
          <code>
            角色 {(payload.roles as unknown[] | undefined)?.length ?? 0}
          </code>
        );
        break;
      case "graph.node.completed":
        title = `节点完成 ${String(payload.node_id ?? "")}`;
        detail = <code>{String(payload.status ?? "")}</code>;
        break;
      case "graph.handoff":
        title = "角色交接";
        detail = (
          <code>
            {String(payload.from_node ?? "")} → {String(payload.to_node ?? "")} ·{" "}
            {(payload.fact_refs as unknown[] | undefined)?.length ?? 0} facts
          </code>
        );
        break;
      case "approval.requested":
        title = `待审批 ${String(payload.tool_ref ?? "")}`;
        detail = <code>{String(payload.reason ?? "")}</code>;
        break;
      case "finding.created":
        title = `新发现 ${String(payload.vuln_category ?? "")}`;
        detail = <code>{String(payload.endpoint ?? "")}</code>;
        break;
      default:
        detail = <code>{JSON.stringify(payload)}</code>;
    }
    return { id: event.event_id, event, dot, icon, title, detail };
  });
}

type ConversationItem = {
  id: string;
  kind: "user" | "agent" | "tool" | "system";
  author: string;
  title?: string;
  body?: ReactNode;
  seq: number | null;
};

function buildConversation(events: AgentEvent[]): ConversationItem[] {
  const items: ConversationItem[] = [];
  for (const event of events) {
    const payload = event.payload as Record<string, unknown>;
    const seq = event.sequence;
    const push = (item: Omit<ConversationItem, "id" | "seq">) => {
      items.push({ ...item, id: event.event_id, seq });
    };
    switch (event.event_type) {
      case "run.submitted":
        push({
          kind: "user",
          author: "你",
          title: "任务意图",
          body: <code>{String(payload.user_input ?? "")}</code>,
        });
        break;
      case "user.message":
        push({
          kind: "user",
          author: "你",
          title: "追加指令",
          body: <code>{String(payload.message ?? "")}</code>,
        });
        break;
      case "model.turn.started":
        push({
          kind: "agent",
          author: "Agent",
          title: `轮次 ${String(payload.turn ?? "?")}`,
          body: payload.reasoning_content
            ? String(payload.reasoning_content)
            : "正在分析目标并规划下一步动作...",
        });
        break;
      case "model.turn.completed":
        push({
          kind: "agent",
          author: "Agent",
          title: `轮次 ${String(payload.turn ?? "?")} 完成`,
          body: payload.text
            ? String(payload.text)
            : payload.tokens
              ? <code>{JSON.stringify(payload.tokens)}</code>
              : undefined,
        });
        break;
      case "tool.proposed":
        push({
          kind: "tool",
          author: "工具",
          title: `提议工具 ${String(payload.tool ?? "")}`,
          body: toolCard(event),
        });
        break;
      case "tool.authorized":
        push({
          kind: "tool",
          author: "工具",
          title: `已授权 ${String(payload.tool ?? "")}`,
          body: <code>rule={String(payload.rule ?? "")}</code>,
        });
        break;
      case "tool.started":
        push({
          kind: "tool",
          author: "工具",
          title: `执行 ${String(payload.tool ?? "")}`,
          body: toolCard(event),
        });
        break;
      case "tool.completed":
        push({
          kind: "tool",
          author: "工具",
          title: `完成 ${String(payload.tool ?? "")}`,
          body: toolCard(event),
        });
        break;
      case "tool.failed":
        push({
          kind: "tool",
          author: "工具",
          title: `失败 ${String(payload.tool ?? "")}`,
          body: <code>{String(payload.error ?? "")}</code>,
        });
        break;
      case "observation.ingested":
        push({
          kind: "tool",
          author: "观测",
          title: `观测摄入 · ${String(payload.tool ?? "")}`,
          body: (
            <>
              <code>
                parsed{" "}
                {(payload.parsed_observations as unknown[] | undefined)?.length ??
                  0}
                {" "}artifacts{" "}
                {(payload.artifact_refs as unknown[] | undefined)?.length ?? 0}
              </code>
              {payload.stdout ? (
                <pre>{String(payload.stdout).slice(0, 500)}</pre>
              ) : null}
            </>
          ),
        });
        break;
      case "finding.created":
        push({
          kind: "system",
          author: "Agent",
          title: `新发现 ${String(payload.vuln_category ?? "")}`,
          body: (
            <code>
              {String(payload.endpoint ?? "")}
              {payload.severity ? ` · ${String(payload.severity)}` : ""}
            </code>
          ),
        });
        break;
      case "approval.requested":
        push({
          kind: "system",
          author: "审批",
          title: `待审批 ${String(payload.tool_ref ?? "")}`,
          body: <code>{String(payload.reason ?? "")}</code>,
        });
        break;
      default:
        if (
          event.event_type.startsWith("graph.") ||
          event.event_type.startsWith("context.") ||
          event.event_type.startsWith("run.")
        ) {
          push({
            kind: "system",
            author: "系统",
            title: event.event_type,
            body: <code>{JSON.stringify(payload).slice(0, 180)}</code>,
          });
        }
    }
  }
  return items;
}

function ConversationView({
  events,
  paused,
  draft,
  sending,
  error,
  onDraftChange,
  onSend,
  chatRef,
}: {
  events: AgentEvent[];
  paused: boolean;
  draft: string;
  sending: boolean;
  error: string | null;
  onDraftChange: (value: string) => void;
  onSend: () => void;
  chatRef: RefObject<HTMLDivElement | null>;
}) {
  const items = useMemo(() => buildConversation(events), [events]);
  const SUGGESTIONS = [
    "继续枚举 /admin 目录",
    "验证认证绕过",
    "生成漏洞验证证据",
  ];
  useEffect(() => {
    const node = chatRef.current;
    if (node) {
      node.scrollTop = node.scrollHeight;
    }
  }, [items.length, chatRef]);
  return (
    <>
      <div className="conversation scroll-panel" ref={chatRef}>
        {items.length === 0 ? (
          <EmptyState
            title="会话尚未开始"
            description="任务启动后，Agent 的分析、工具调用与反馈会在这里呈现。"
          />
        ) : (
          items.map((item) => (
            <div className={`msg msg-${item.kind}`} key={item.id}>
              <div className="msg-head">
                {item.kind === "user" ? (
                  <User className="" />
                ) : (
                  <Sparkles className="" />
                )}
                <span className="msg-author">{item.author}</span>
                {item.title ? (
                  <span className="msg-title">{item.title}</span>
                ) : null}
                <span className="activity-time">#{item.seq}</span>
              </div>
              {item.body ? <div className="msg-body">{item.body}</div> : null}
            </div>
          ))
        )}
      </div>
      {paused ? (
        <div className="suggestion-chips">
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              className="btn btn-sm"
              onClick={() => onDraftChange(suggestion)}
            >
              {suggestion}
            </button>
          ))}
        </div>
      ) : null}
      <div className="msg-input-row">
        <textarea
          rows={2}
          aria-label="向 Agent 发送指令"
          placeholder={
            paused
              ? "向 Agent 发送追加指令，例如：对 /admin 目录做进一步枚举..."
              : "运行中请先暂停，再向 Agent 发送指令"
          }
          value={draft}
          disabled={!paused || sending}
          onChange={(event) => onDraftChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              if (paused && draft.trim()) {
                onSend();
              }
            }
          }}
        />
        <button
          className="btn btn-primary"
          disabled={!paused || sending || !draft.trim()}
          onClick={onSend}
        >
          <Send className="" />
          发送
        </button>
      </div>
      {!paused ? (
        <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
          运行中不可发送；先暂停运行即可继续对话。
        </p>
      ) : null}
      {error ? (
        <div style={{ marginTop: 8 }}>
          <Notice tone="error">{error}</Notice>
        </div>
      ) : null}
    </>
  );
}

function RunTraceView({ trace }: { trace: Record<string, unknown> | undefined }) {
  const events = (trace?.events as Array<Record<string, unknown>> | undefined) ?? [];
  const toolEvents =
    (trace?.tool_events as Array<Record<string, unknown>> | undefined) ?? [];
  const findings =
    (trace?.findings as Array<Record<string, unknown>> | undefined) ?? [];
  const metrics =
    (trace?.graph_metrics as Array<Record<string, unknown>> | undefined) ?? [];
  const visibleEvents = events.slice(-40).reverse();
  return (
    <div className="stack" style={{ gap: 10 }}>
      {trace ? (
        <div className="actions">
          <button
            className="btn"
            onClick={() => {
              const blob = new Blob(
                [JSON.stringify(trace, null, 2)],
                { type: "application/json" },
              );
              const url = URL.createObjectURL(blob);
              const anchor = document.createElement("a");
              anchor.href = url;
              anchor.download = `trace-${String(
                trace.run_id ?? "run",
              )}.json`;
              document.body.appendChild(anchor);
              anchor.click();
              anchor.remove();
              URL.revokeObjectURL(url);
            }}
          >
            导出 Trace JSON
          </button>
        </div>
      ) : null}
      <div className="kpi-grid" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
        <Kpi label="事件" value={events.length} tone="info" />
        <Kpi label="工具调用" value={toolEvents.length} tone="ok" />
        <Kpi label="Findings" value={findings.length} tone="info" />
        <Kpi label="图完成" value={metrics.length} tone="ok" />
      </div>
      <Panel title="事件时间线" icon={Activity}>
        <div className="activity-feed scroll-panel">
          {visibleEvents.length === 0 ? (
            <EmptyState title="暂无轨迹" description="运行开始后生成轨迹。" />
          ) : (
            visibleEvents.map((event) => (
              <div className="activity-item" key={String(event.event_id ?? event.sequence)}>
                <div className="activity-dot info">
                  <Activity className="" />
                </div>
                <div className="activity-body">
                  <div className="activity-head">
                    <span className="activity-title">
                      {String(event.event_type)}
                    </span>
                    <span className="activity-time">
                      #{String(event.sequence ?? "")}
                    </span>
                  </div>
                  <div className="activity-detail">
                    <code>
                      {JSON.stringify(event.payload ?? {}).slice(0, 180)}
                    </code>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </Panel>
      {findings.length ? (
        <Panel title="结构化发现" icon={ShieldCheck}>
          <div className="stack" style={{ gap: 8 }}>
            {findings.slice(0, 10).map((finding) => (
              <div className="card" key={String(finding.finding_id)}>
                <div className="panel-head" style={{ marginBottom: 4 }}>
                  <div className="card-title" style={{ margin: 0 }}>
                    {String(finding.vuln_category)}
                  </div>
                  <Badge value={String(finding.status)}>
                    {String(finding.status)}
                  </Badge>
                </div>
                <p className="card-meta" style={{ margin: 0 }}>
                  {String(finding.endpoint)}
                </p>
              </div>
            ))}
          </div>
        </Panel>
      ) : null}
    </div>
  );
}

export function RunCockpit() {
  const runId = useRunSelection((state) => state.selectedRunId);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [eventFilter, setEventFilter] = useState("");
  const [eventGroup, setEventGroup] = useState("all");
  const [forkedRunId, setForkedRunId] = useState<string | null>(null);
  const [leftTab, setLeftTab] = useState<"chat" | "activity" | "trace">("chat");
  const [draft, setDraft] = useState("");
  const chatRef = useRef<HTMLDivElement | null>(null);
  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => control.getRun(runId!),
    enabled: Boolean(runId),
    refetchInterval: 2000,
  });
  const mission = useQuery({
    queryKey: ["mission", run.data?.mission_id],
    queryFn: () => control.getMission(run.data!.mission_id),
    enabled: Boolean(run.data?.mission_id),
  });
  const approvals = useQuery({
    queryKey: ["approvals", runId],
    queryFn: () => control.listApprovals(runId!),
    enabled: Boolean(runId),
    refetchInterval: 3000,
  });
  const events = useQuery({
    queryKey: ["events", runId],
    queryFn: () => control.getEvents(runId!, 0),
    enabled: Boolean(runId),
    refetchInterval: 2000,
  });
  const trace = useQuery({
    queryKey: ["run-trace", runId],
    queryFn: () =>
      control.requestPublic(`/api/v1/runs/${runId}/trace`),
    enabled: Boolean(runId),
    refetchInterval: 4000,
  });
  useEffect(() => {
    if (!runId) {
      return;
    }
    const source = new EventSource(
      `${CONTROL_URL}/api/v1/runs/${runId}/events/stream?after=0`,
    );
    source.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as AgentEvent;
        queryClient.setQueryData<AgentEvent[]>(["events", runId], (old) => {
          if (!event?.event_id) {
            return old;
          }
          const seen = new Set((old ?? []).map((item) => item.event_id));
          return seen.has(event.event_id) ? old : [...(old ?? []), event];
        });
      } catch {
        // ignore malformed frames; polling fallback keeps data fresh
      }
    };
    return () => source.close();
  }, [runId, queryClient]);
  const graphMetrics = useQuery({
    queryKey: ["graph-metrics", runId],
    queryFn: () =>
      control.requestPublic(`/api/v1/runs/${runId}/graph-metrics`),
    enabled: Boolean(runId),
    refetchInterval: 5000,
  });
  const command = useMutation({
    mutationFn: (action: "pause" | "resume" | "cancel") =>
      control.runCommand(runId!, action, crypto.randomUUID()),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["run", runId] });
      void queryClient.invalidateQueries({ queryKey: ["events", runId] });
    },
  });
  const fork = useMutation({
    mutationFn: () => control.forkRun(runId!, crypto.randomUUID()),
    onSuccess: (forked) => {
      setForkedRunId(forked.run_id);
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
  });
  const takeover = useMutation({
    mutationFn: () =>
      control.takeoverRun(runId!, "web-operator", crypto.randomUUID()),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["run", runId] });
    },
  });
  const sendMessage = useMutation({
    mutationFn: (message: string) =>
      control.sendMessage(
        runId!,
        message,
        crypto.randomUUID(),
        "web-operator",
      ),
    onSuccess: () => {
      setDraft("");
      void queryClient.invalidateQueries({ queryKey: ["run", runId] });
      void queryClient.invalidateQueries({ queryKey: ["events", runId] });
    },
  });
  const decide = useMutation({
    mutationFn: (args: { id: string; approved: boolean }) =>
      control.decideApproval(args.id, args.approved, "web-operator"),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["approvals", runId] });
    },
  });

  const rolePolicies = useMemo(
    () =>
      (events.data ?? [])
        .filter((event) => event.event_type === "graph.started")
        .flatMap(
          (event) =>
            (event.payload.roles as
              | Array<{
                  role_id: string;
                  budget: Record<string, unknown>;
                }>
              | undefined) ?? [],
        ),
    [events.data],
  );
  const policyByRole = useMemo(
    () => new Map(rolePolicies.map((role) => [role.role_id, role.budget])),
    [rolePolicies],
  );
  const projectionEvents = useMemo(
    () =>
      (events.data ?? []).filter(
        (event) => event.event_type === "context.projection",
      ),
    [events.data],
  );
  const memoryProjection = projectionEvents.length
    ? (
        projectionEvents[projectionEvents.length - 1].payload as {
          memory?: MemoryProjection;
        }
      ).memory
    : undefined;
  const graphNodes = useMemo(
    () =>
      (events.data ?? [])
        .filter((event) => event.event_type === "graph.node.completed")
        .map((event) => {
          const roleId = String(event.payload.node_id);
          const budget = policyByRole.get(roleId);
          return {
            id: roleId,
            status: String(event.payload.status),
            detail: budget
              ? `oracle=${String(budget.oracle ?? "-")}`
              : undefined,
          };
        }),
    [events.data, policyByRole],
  );
  const graphEdges = useMemo(
    () =>
      (events.data ?? [])
        .filter((event) => event.event_type === "graph.handoff")
        .map((event) => ({
          from: String(event.payload.from_node),
          to: String(event.payload.to_node),
          label: `facts ${String(
            (event.payload.fact_refs as unknown[] | undefined)?.length ?? 0,
          )}`,
        })),
    [events.data],
  );
  const activity = useMemo(() => buildActivity(events.data ?? []), [events.data]);
  const remoteExecution = useMemo(() => {
    const list = (events.data ?? []).filter((event) =>
      [
        "run.remote_dispatched",
        "run.remote_result_received",
        "run.remote_dispatch_failed",
      ].includes(event.event_type),
    );
    if (list.length === 0) {
      return null;
    }
    const latest = list[list.length - 1];
    const status =
      latest.event_type === "run.remote_result_received"
        ? String(latest.payload?.status ?? "done")
        : latest.event_type === "run.remote_dispatch_failed"
          ? "failed"
          : "dispatched";
    return {
      nodeId: String(latest.payload?.node_id ?? ""),
      status,
      leaseId: String(latest.payload?.lease_id ?? ""),
      resultId: String(latest.payload?.result_id ?? ""),
      count: list.length,
    };
  }, [events.data]);
  const filteredActivity = useMemo(() => {
    const text = eventFilter.trim().toLowerCase();
    return activity.filter((item) => {
      const type = item.event.event_type;
      const group = type.split(".")[0];
      if (eventGroup !== "all" && group !== eventGroup) {
        return false;
      }
      if (!text) {
        return true;
      }
      return (
        type.toLowerCase().includes(text) ||
        item.title.toLowerCase().includes(text) ||
        JSON.stringify(item.event.payload).toLowerCase().includes(text)
      );
    });
  }, [activity, eventFilter, eventGroup]);
  const phases = useMemo(
    () => summarizePhases(events.data ?? [], run.data?.status ?? "queued"),
    [events.data, run.data?.status],
  );
  const pendingApprovals = (approvals.data ?? []).filter(
    (approval) => approval.state === "requested",
  );
  const missionData = mission.data as
    | { name?: string; spec?: Record<string, unknown> }
    | undefined;
  const metrics = (graphMetrics.data?.metrics as
    | Array<Record<string, unknown>>
    | undefined) ?? [];
  const latestProjection = projectionEvents.length
    ? (projectionEvents[projectionEvents.length - 1].payload as {
        knowledge?: { included?: unknown[] };
        skills?: {
          included?: Array<{ name?: string; score?: number }>;
          channels?: string[];
        };
        mcp?: { included?: unknown[] };
        token_estimate?: number;
        rag_degraded?: unknown[];
      })
    : undefined;

  if (!runId) {
    return (
      <section>
        <header className="page-head">
          <div className="page-head-copy">
            <p className="page-eyebrow">Run Cockpit</p>
            <h1>运行控制台</h1>
            <p className="page-sub">
              选择一个运行以查看实时事件、图编排和指标。
            </p>
          </div>
        </header>
        <RunPicker />
      </section>
    );
  }
  if (run.isLoading || events.isLoading) {
    return <Loading label="加载运行控制台" />;
  }
  if (run.isError) {
    return <ErrorBanner message={String(run.error)} />;
  }
  const memoryFacts = memoryProjection?.facts ?? [];

  return (
    <section>
      <header className="page-head">
        <div className="page-head-copy">
          <p className="page-eyebrow">Agent Console</p>
          <h1>运行控制台</h1>
          <p className="page-sub">
            <code>{run.data?.run_id}</code> / <code>{run.data?.mission_id}</code>
          </p>
          <SyncStamp dataUpdatedAt={events.dataUpdatedAt} />
        </div>
        <div className="actions">
          <Badge value={run.data?.status}>{run.data?.status}</Badge>
          <button
            className="btn"
            onClick={() => {
              setForkedRunId(null);
              void queryClient.invalidateQueries({ queryKey: ["run", runId] });
              void queryClient.invalidateQueries({ queryKey: ["events", runId] });
            }}
          >
            <RefreshCw className="" />
            刷新
          </button>
        </div>
      </header>
      <div className="kpi-grid">
        <Kpi
          label="事件数"
          value={events.data?.length ?? 0}
          tone="info"
          note="当前拉取窗口"
        />
        <Kpi
          label="图节点"
          value={graphNodes.length}
          tone={graphNodes.length ? "ok" : undefined}
          note="角色节点"
        />
        <Kpi
          label="角色交接"
          value={graphEdges.length}
          tone={graphEdges.length ? "info" : undefined}
          note="handoff"
        />
        <Kpi
          label="内存事实"
          value={memoryProjection?.snapshot?.total_facts ?? memoryFacts.length}
          note={`active ${memoryProjection?.snapshot?.active ?? 0}`}
        />
        <Kpi
          label="路径效率"
          value={
            metrics.length
              ? `${(Number(metrics[metrics.length - 1].path_efficiency ?? 0) * 100).toFixed(0)}%`
              : "-"
          }
          tone="ok"
          note="graph metrics"
        />
      </div>
      <div className="cockpit-grid">
        <div className="cockpit-column">
          <Panel
            title={
              leftTab === "chat"
                ? "Agent 会话"
                : leftTab === "activity"
                  ? "Agent 活动"
                  : "Run 轨迹"
            }
            icon={leftTab === "chat" ? Sparkles : Activity}
            actions={
              <>
                <span className="muted" style={{ fontSize: 11 }}>
                  更新{" "}
                  {events.dataUpdatedAt
                    ? new Date(events.dataUpdatedAt).toLocaleTimeString()
                    : "-"}
                </span>
                <div className="btn-group">
                  <button
                    className={`btn btn-sm${leftTab === "chat" ? " btn-primary" : ""}`}
                    onClick={() => setLeftTab("chat")}
                  >
                    会话
                  </button>
                  <button
                    className={`btn btn-sm${leftTab === "activity" ? " btn-primary" : ""}`}
                    onClick={() => setLeftTab("activity")}
                  >
                    活动
                  </button>
                  <button
                    className={`btn btn-sm${leftTab === "trace" ? " btn-primary" : ""}`}
                    onClick={() => setLeftTab("trace")}
                  >
                    轨迹
                  </button>
                </div>
              </>
            }
          >
            {leftTab === "trace" ? (
              <RunTraceView
                trace={
                  trace.data as Record<string, unknown> | undefined
                }
              />
            ) : leftTab === "chat" ? (
              <ConversationView
                events={events.data ?? []}
                paused={run.data?.status === "paused"}
                draft={draft}
                sending={sendMessage.isPending}
                error={sendMessage.isError ? String(sendMessage.error) : null}
                onDraftChange={setDraft}
                onSend={() => {
                  if (draft.trim()) {
                    sendMessage.mutate(draft.trim());
                  }
                }}
                chatRef={chatRef}
              />
            ) : (
              <>
                <div className="toolbar" style={{ marginBottom: 8 }}>
                  <Search className="" style={{ width: 14, height: 14, color: "var(--muted)" }} />
                  <input
                    type="text"
                    aria-label="过滤活动"
                    placeholder="过滤活动"
                    value={eventFilter}
                    onChange={(event) => setEventFilter(event.target.value)}
                  />
                  <select
                    value={eventGroup}
                    onChange={(event) => setEventGroup(event.target.value)}
                    aria-label="事件分组"
                    style={{ flex: "0 0 auto", width: 110 }}
                  >
                    <option value="all">全部</option>
                    <option value="run">运行</option>
                    <option value="model">模型</option>
                    <option value="tool">工具</option>
                    <option value="context">上下文</option>
                    <option value="graph">图</option>
                    <option value="observation">观测</option>
                  </select>
                </div>
                <div className="activity-feed scroll-panel">
                  {filteredActivity.length === 0 ? (
                    <div className="status-line" style={{ padding: 14 }}>
                      暂无匹配活动
                    </div>
                  ) : (
                    filteredActivity.map((item) => (
                      <div className="activity-item" key={item.id}>
                        <div className={`activity-dot ${item.dot}`}>
                          <item.icon className="" />
                        </div>
                        <div className="activity-body">
                          <div className="activity-head">
                            <span className="activity-title">{item.title}</span>
                            <span className="activity-time">
                              #{item.event.sequence} · {item.event.event_type}
                            </span>
                          </div>
                          {item.detail ? (
                            <div className="activity-detail">{item.detail}</div>
                          ) : null}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </>
            )}
          </Panel>
        </div>
        <div className="cockpit-column">
          <Panel
            title="攻击图编排"
            icon={ListTree}
            actions={
              <span className="muted" style={{ fontSize: 12 }}>
                双击复位
              </span>
            }
          >
            {graphNodes.length > 0 ? (
              <GraphCanvas nodes={graphNodes} edges={graphEdges} />
            ) : (
              <EmptyState
                title="图尚未展开"
                description="运行开始后，角色节点与交接会显示在这里。"
              />
            )}
          </Panel>
          {latestProjection ? (
            <Panel title="上下文投影" icon={Layers}>
              <div className="memory-summary">
                <span>知识 {(latestProjection.knowledge?.included ?? []).length}</span>
                <span>技能 {(latestProjection.skills?.included ?? []).length}</span>
                <span>MCP {(latestProjection.mcp?.included ?? []).length}</span>
                <span>token ≈ {latestProjection.token_estimate ?? 0}</span>
                {latestProjection.skills?.channels?.length ? (
                  <span>
                    检索 {latestProjection.skills.channels.join(" + ")}
                  </span>
                ) : null}
                {latestProjection.rag_degraded?.length ? (
                  <span className="text-warn">RAG 降级</span>
                ) : null}
              </div>
              {latestProjection.skills?.included?.length ? (
                <ul
                  className="muted"
                  style={{
                    margin: "8px 0 0",
                    paddingLeft: 18,
                    fontSize: 12,
                  }}
                >
                  {latestProjection.skills.included.map((skill) => (
                    <li key={String(skill.name ?? "")}>
                      {String(skill.name ?? "")}
                      {typeof skill.score === "number"
                        ? ` · ${skill.score.toFixed(4)}`
                        : ""}
                    </li>
                  ))}
                </ul>
              ) : null}
              <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
                上下文由知识、技能、MCP 与记忆投影组装，供模型轮次使用。
              </p>
            </Panel>
          ) : null}
          {remoteExecution ? (
            <Panel title="远程执行" icon={Server}>
              <div className="memory-summary">
                <span>node {remoteExecution.nodeId}</span>
                <Badge value={remoteExecution.status}>
                  {remoteExecution.status}
                </Badge>
                <span>{remoteExecution.count} 条事件</span>
              </div>
              {remoteExecution.leaseId ? (
                <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
                  lease <code>{remoteExecution.leaseId}</code>
                  {remoteExecution.resultId
                    ? ` · result ${remoteExecution.resultId}`
                    : ""}
                </p>
              ) : null}
            </Panel>
          ) : null}
          <Panel title="角色策略" icon={ShieldCheck}>
            {rolePolicies.length === 0 ? (
              <p className="muted" style={{ marginBottom: 0 }}>
                尚无角色策略事件。
              </p>
            ) : (
              <div className="stack" style={{ gap: 8 }}>
                {rolePolicies.map((role) => (
                  <div className="card" key={role.role_id}>
                    <div className="card-title">
                      <code>{role.role_id}</code>
                    </div>
                    <div className="card-meta">
                      oracle={String(role.budget.oracle ?? "-")}
                      {role.budget.required_categories
                        ? ` required=${String(role.budget.required_categories)}`
                        : ""}
                      {role.budget.min_severity
                        ? ` severity>=${String(role.budget.min_severity)}`
                        : ""}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>
          {memoryProjection ? (
            <Panel title="项目记忆" icon={BrainCircuit}>
              <div className="memory-summary">
                <span>active {memoryProjection.snapshot?.active ?? 0}</span>
                <span>conflict {memoryProjection.snapshot?.conflict ?? 0}</span>
                <span>stale {memoryProjection.snapshot?.stale ?? 0}</span>
                <span>total {memoryProjection.snapshot?.total_facts ?? 0}</span>
              </div>
              {memoryProjection.digest ? (
                <p className="muted" style={{ fontSize: 12, wordBreak: "break-all" }}>
                  digest <code>{memoryProjection.digest}</code>
                </p>
              ) : null}
              <div className="table-wrap scroll-panel">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Fact</th>
                      <th>Subject</th>
                      <th>Predicate</th>
                      <th>Value</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {memoryFacts.map((fact: MemoryFactRow) => (
                      <tr key={fact.fact_id}>
                        <td className="mono">{fact.fact_id.slice(0, 12)}</td>
                        <td>{fact.subject}</td>
                        <td>{fact.predicate}</td>
                        <td>{fact.value}</td>
                        <td>
                          <Badge value={fact.status}>{fact.status}</Badge>
                        </td>
                      </tr>
                    ))}
                    {memoryFacts.length === 0 ? (
                      <tr>
                        <td colSpan={5} className="muted">
                          暂无内存事实
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </Panel>
          ) : null}
        </div>
        <div className="cockpit-column">
          <Panel title="安全流程进度" icon={Radar}>
            <div className="workflow-stepper">
              {phases.map((phase) => (
                <div className={`workflow-step workflow-${phase.state}`} key={phase.id}>
                  <div className="workflow-marker">
                    <phase.icon className="" />
                  </div>
                  <div className="workflow-body">
                    <div className="workflow-label">
                      <span>{phase.label}</span>
                      <Badge value={phase.state}>{phase.state}</Badge>
                    </div>
                    <div className="workflow-desc">
                      <span>{phase.desc}</span>
                      <span>{phase.count} 条相关事件</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </Panel>
          <Panel
            title="审批待办"
            icon={ShieldCheck}
            actions={
              <span className="muted" style={{ fontSize: 12 }}>
                {pendingApprovals.length} 条
              </span>
            }
          >
            {pendingApprovals.length === 0 ? (
              <p className="muted" style={{ marginBottom: 0 }}>
                当前没有待审批的工具调用。
              </p>
            ) : (
              <div className="stack" style={{ gap: 8 }}>
                {pendingApprovals.map((approval) => (
                  <div className="card" key={approval.approval_id}>
                    <div className="card-title">
                      <code>{approval.tool_ref}</code>
                    </div>
                    <p className="card-meta" style={{ marginBottom: 8 }}>
                      风险 {approval.risk_level} · {approval.reason}
                    </p>
                    <div className="btn-group">
                      <button
                        className="btn btn-sm btn-primary"
                        onClick={() =>
                          decide.mutate({ id: approval.approval_id, approved: true })
                        }
                      >
                        批准
                      </button>
                      <button
                        className="btn btn-sm btn-danger"
                        onClick={() => {
                          if (
                            window.confirm(
                              `确定拒绝 ${approval.tool_ref} 的工具调用？`,
                            )
                          ) {
                            decide.mutate({
                              id: approval.approval_id,
                              approved: false,
                            });
                          }
                        }}
                      >
                        拒绝
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {decide.isError ? (
              <div style={{ marginTop: 8 }}>
                <Notice tone="error">{String(decide.error)}</Notice>
              </div>
            ) : null}
          </Panel>
          <Panel title="任务信息" icon={Copy}>
            <div className="card-meta" style={{ display: "grid", gap: 7 }}>
              <div>
                mission: <code>{run.data?.mission_id}</code>
              </div>
              {missionData?.name ? (
                <div>name: {missionData.name}</div>
              ) : null}
              {missionData?.spec?.mission ? (
                <div>
                  意图:
                  <p className="muted" style={{ margin: "2px 0 0", fontSize: 12 }}>
                    {String(missionData.spec.mission)}
                  </p>
                </div>
              ) : null}
              {missionData?.spec?.max_turns !== undefined ? (
                <div>max_turns: {String(missionData.spec.max_turns)}</div>
              ) : null}
              {missionData?.spec?.required_categories ? (
                <div>
                  required:{" "}
                  {Array.isArray(missionData.spec.required_categories)
                    ? (missionData.spec.required_categories as unknown[]).join(", ")
                    : String(missionData.spec.required_categories)}
                </div>
              ) : null}
            </div>
          </Panel>
          <Panel title="运行控制" icon={Zap}>
            <div className="form-section">
              <div className="form-section-title">状态命令</div>
              <div className="btn-group">
                <button
                  className="btn"
                  onClick={() => command.mutate("pause")}
                  disabled={command.isPending}
                >
                  <CirclePause className="" />
                  暂停
                </button>
                <button
                  className="btn"
                  onClick={() => command.mutate("resume")}
                  disabled={command.isPending}
                >
                  <CirclePlay className="" />
                  恢复
                </button>
                <button
                  className="btn btn-danger"
                  onClick={() => {
                    if (
                      window.confirm(
                        "确定取消该运行？已执行结果会保留，但任务不会再继续。",
                      )
                    ) {
                      command.mutate("cancel");
                    }
                  }}
                  disabled={command.isPending}
                >
                  <CircleStop className="" />
                  取消
                </button>
              </div>
            </div>
            <div className="form-section" style={{ marginBottom: 0 }}>
              <div className="form-section-title">会话操作</div>
              <div className="btn-group">
                <button
                  className="btn"
                  onClick={() => fork.mutate()}
                  disabled={fork.isPending}
                >
                  <GitFork className="" />
                  Fork
                </button>
                <button
                  className="btn"
                  onClick={() => takeover.mutate()}
                  disabled={takeover.isPending}
                >
                  <Hand className="" />
                  Takeover
                </button>
              </div>
            </div>
            {forkedRunId ? (
              <div style={{ marginTop: 12 }}>
                <Notice tone="ok">
                  Forked run: <code>{forkedRunId}</code>
                </Notice>
              </div>
            ) : null}
            {run.data?.stop_reason ? (
              <div style={{ marginTop: 12 }}>
                <Notice tone="warn">停止原因：{run.data.stop_reason}</Notice>
              </div>
            ) : null}
          </Panel>
          {metrics.length > 0 ? (
            <Panel title="Graph Metrics" icon={Activity}>
              {metrics.map((metric, index) => {
                const efficiency = Number(metric.path_efficiency ?? 0);
                return (
                  <div key={index} className="metric-row">
                    <span>handoffs {String(metric.handoffs)}</span>
                    <span>dead {String(metric.dead_letters)}</span>
                    <span>dup {String(metric.duplicate_actions)}</span>
                    <div className="metric-bar">
                      <div
                        style={{
                          width: `${Math.max(
                            0,
                            Math.min(100, Math.round(efficiency * 100)),
                          )}%`,
                        }}
                      />
                    </div>
                    <span>{efficiency.toFixed(2)}</span>
                  </div>
                );
              })}
            </Panel>
          ) : null}
          <Panel title="运行信息" icon={Copy}>
            <div className="card-meta" style={{ display: "grid", gap: 7 }}>
              <div>
                run id: <code>{run.data?.run_id}</code>
              </div>
              <div>
                mission: <code>{run.data?.mission_id}</code>
              </div>
              <div>created: {run.data?.created_at}</div>
              <div>events: {run.data?.event_count}</div>
            </div>
          </Panel>
        </div>
      </div>
    </section>
  );
}
