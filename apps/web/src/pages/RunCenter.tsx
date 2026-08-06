import { useMemo, useState } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  Activity,
  ArrowUpRight,
  CirclePause,
  CirclePlay,
  CircleStop,
  Download,
  Filter,
  Play,
  PlusCircle,
  Radar,
  Search,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import { control, CONTROL_URL } from "../api.js";
import { ErrorBanner, Loading, OfflineBanner } from "../components/Status.js";
import { useRunSelection } from "../store.js";
import {
  Badge,
  EmptyState,
  Kpi,
  LiveDot,
  Notice,
  Panel,
  SyncStamp,
} from "../components/ui.js";

type RunRow = {
  run_id: string;
  mission_id: string;
  status: string;
  event_count: number;
  created_at?: string;
  stop_reason?: string | null;
  source_run_id?: string | null;
};

const ACTIVE_STATUSES = new Set(["requested", "running", "claimed", "paused"]);
const DONE_STATUSES = new Set(["succeeded", "failed", "cancelled"]);

function isValidUrl(value: string): boolean {
  const text = value.trim();
  if (!text) {
    return false;
  }
  try {
    const url = new URL(text);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

export function RunCenter() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const setSelectedRunId = useRunSelection((state) => state.setSelectedRunId);
  const [statusFilter, setStatusFilter] = useState("all");
  const [projectFilter, setProjectFilter] = useState("");
  const [query, setQuery] = useState("");
  const [quickTarget, setQuickTarget] = useState("https://lab.example.test");
  const [quickMission, setQuickMission] = useState("web discovery");
  const [quickIntent, setQuickIntent] = useState(
    "对目标执行 Web 探测，识别并验证常见 Web 漏洞，整理可复现证据。",
  );
  const [quickTemplate, setQuickTemplate] = useState<"web" | "code">("web");
  const selectQuickTemplate = (template: "web" | "code") => {
    setQuickTemplate(template);
    if (template === "code") {
      setQuickTarget("/workspace/input");
      setQuickMission("code audit");
      setQuickIntent(
        "对挂载源码执行 SAST 与密钥扫描，整理结构化代码审计发现。",
      );
    } else {
      setQuickTarget("https://lab.example.test");
      setQuickMission("web discovery");
      setQuickIntent(
        "对目标执行 Web 探测，识别并验证常见 Web 漏洞，整理可复现证据。",
      );
    }
  };
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: () => control.listRuns(),
    refetchInterval: 3000,
  });
  const diagnostics = useQuery({
    queryKey: ["diagnostics"],
    queryFn: () => control.requestPublic("/api/v1/diagnostics"),
    refetchInterval: 8000,
  });
  const rows = runs.data ?? [];
  const missions = useQuery({
    queryKey: ["missions"],
    queryFn: () => control.requestPublic("/api/v1/missions"),
  });
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => control.listProjects(),
  });
  const missionByRun = useMemo(() => {
    const map = new Map<
      string,
      {
        name?: string;
        project_id?: string;
        spec?: { mission?: string };
      }
    >();
    const byId = new Map(
      ((missions.data ?? []) as Array<{
        mission_id: string;
        name?: string;
        project_id?: string;
        spec?: { mission?: string };
      }>).map((mission) => [mission.mission_id, mission]),
    );
    rows.forEach((run) => {
      const mission = byId.get(run.mission_id);
      if (mission) {
        map.set(run.run_id, mission);
      }
    });
    return map;
  }, [rows, missions.data]);
  const latestRunId = rows[0]?.run_id;
  const latestEvents = useQuery({
    queryKey: ["events", latestRunId],
    queryFn: () => control.getEvents(latestRunId!, 0),
    enabled: Boolean(latestRunId),
    refetchInterval: 3000,
  });
  const quickStart = useMutation({
    mutationFn: async () => {
      const project = await control.createProject(`quick-${Date.now()}`);
      const target = await control.createTarget(
        project.project_id,
        quickTarget.trim(),
      );
      const spec: Record<string, unknown> = {
        target_id: target.target_id,
        mission: quickIntent,
        max_turns: quickTemplate === "code" ? 8 : 5,
        streaming: false,
      };
      if (quickTemplate === "code") {
        spec.mode = "multi_role";
        spec.role_template = "code_audit";
        spec.required_categories = ["security", "HardcodedSecret"];
        spec.min_severity = "low";
        spec.code_tools = ["code.sast.semgrep", "code.secrets.detect"];
        spec.scanner_tools = ["code.sast.semgrep", "code.secrets.detect"];
        spec.allowed_tools = [
          "code.sast.semgrep",
          "code.secrets.detect",
          "run.finish",
        ];
        const pathArgs = {
          "code.sast.semgrep": { path: "/workspace/input" },
          "code.secrets.detect": { path: "/workspace/input" },
        };
        spec.tool_args = pathArgs;
        spec.forced_tool_args = pathArgs;
      }
      const mission = await control.createMission(
        project.project_id,
        quickMission.trim(),
        spec,
      );
      const run = await control.startRun(
        mission.mission_id,
        crypto.randomUUID(),
      );
      return run;
    },
    onSuccess: (run) => {
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      navigate("/cockpit");
      setSelectedRunId(run.run_id);
    },
  });
  const runCommand = useMutation({
    mutationFn: (args: { runId: string; action: "pause" | "resume" | "cancel" }) =>
      control.runCommand(args.runId, args.action, crypto.randomUUID()),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
  });

  const filtered = useMemo(
    () =>
      rows.filter((run) => {
        const matchesStatus =
          statusFilter === "all" ||
          (statusFilter === "active" && ACTIVE_STATUSES.has(run.status)) ||
          (statusFilter === "done" && DONE_STATUSES.has(run.status)) ||
          (statusFilter === "failed" &&
            ["failed", "cancelled"].includes(run.status));
        if (!matchesStatus) {
          return false;
        }
        if (
          projectFilter &&
          missionByRun.get(run.run_id)?.project_id !== projectFilter
        ) {
          return false;
        }
        const text = query.trim().toLowerCase();
        if (!text) {
          return true;
        }
        return (
          run.run_id.toLowerCase().includes(text) ||
          run.mission_id.toLowerCase().includes(text)
        );
      }),
    [rows, statusFilter, projectFilter, query, missionByRun],
  );
  const activeCount = rows.filter((run) => ACTIVE_STATUSES.has(run.status)).length;
  const doneCount = rows.filter((run) => DONE_STATUSES.has(run.status)).length;
  const failedCount = rows.filter((run) =>
    ["failed", "cancelled"].includes(run.status),
  ).length;
  const totalEvents = rows.reduce((sum, run) => sum + run.event_count, 0);
  const providers = (
    diagnostics.data as
      | { providers?: Array<{ provider_id: string; model: string; status: string }> }
      | undefined
  )?.providers ?? [];
  const workers = (diagnostics.data as { worker?: { status: string } } | undefined)
    ?.worker;
  const quickTargetValid =
    quickTemplate === "code"
      ? Boolean(quickTarget.trim())
      : isValidUrl(quickTarget);
  const quickIntentValid = quickIntent.trim().length >= 8;
  const quickMissionValid = Boolean(quickMission.trim());
  const quickReady = quickTargetValid && quickIntentValid && quickMissionValid;

  return (
    <section>
      <OfflineBanner />
      <header className="page-head">
        <div className="page-head-copy">
          <p className="page-eyebrow">Mission Center</p>
          <h1>任务中心</h1>
          <p className="page-sub">
            启动新任务、跟踪运行状态并实时查看 Agent 活动。
          </p>
          <SyncStamp dataUpdatedAt={runs.dataUpdatedAt} />
        </div>
        <div className="actions">
          <button className="btn btn-primary" onClick={() => navigate("/setup")}>
            <PlusCircle className="" />
            高级新建任务
          </button>
        </div>
      </header>
      <div className="mission-hero">
        <h2>快速启动</h2>
        <p className="page-sub">
          输入目标与任务意图，立即创建项目、目标并启动一次 Agent 运行。
        </p>
        <div className="tabs" style={{ marginBottom: 12, borderBottom: 0 }}>
          <button
            className={`tab${quickTemplate === "web" ? " active" : ""}`}
            onClick={() => selectQuickTemplate("web")}
          >
            Web 扫描
          </button>
          <button
            className={`tab${quickTemplate === "code" ? " active" : ""}`}
            onClick={() => selectQuickTemplate("code")}
          >
            代码审计
          </button>
        </div>
        <div className="form-grid">
          <label className="field">
            {quickTemplate === "code" ? "代码路径" : "目标 URL"}
            <input
              value={quickTarget}
              onChange={(event) => setQuickTarget(event.target.value)}
              placeholder={
                quickTemplate === "code"
                  ? "/workspace/input"
                  : "https://lab.example.test"
              }
            />
            {!quickTargetValid ? (
              <span className="field-error">
                {quickTemplate === "code"
                  ? "请输入代码路径，例如 /workspace/input"
                  : "请输入 http(s):// 开头的目标 URL"}
              </span>
            ) : null}
          </label>
          <label className="field">
            任务名称
            <input
              value={quickMission}
              onChange={(event) => setQuickMission(event.target.value)}
            />
            {!quickMissionValid ? (
              <span className="field-error">任务名称不能为空</span>
            ) : null}
          </label>
          <label className="field" style={{ gridColumn: "1 / -1" }}>
            任务意图（自然语言）
            <textarea
              rows={2}
              value={quickIntent}
              onChange={(event) => setQuickIntent(event.target.value)}
              placeholder="描述希望 Agent 执行的安全测试目标"
            />
            {!quickIntentValid ? (
              <span className="field-error">任务意图至少 8 个字符</span>
            ) : null}
          </label>
          <div className="actions" style={{ alignItems: "flex-end", marginBottom: 0 }}>
            <button
              className="btn btn-primary"
              disabled={quickStart.isPending || !quickReady}
              title={
                quickReady
                  ? "创建并启动一次 Agent 运行"
                  : "请先补全目标 URL、任务名称与任务意图"
              }
              onClick={() => quickStart.mutate()}
            >
              <Play className="" />
              {quickStart.isPending ? "启动中..." : "启动任务"}
            </button>
          </div>
        </div>
        {quickStart.isError ? (
          <div style={{ marginTop: 12 }}>
            <Notice tone="error">{String(quickStart.error)}</Notice>
          </div>
        ) : null}
      </div>
      <div className="kpi-grid">
        <Kpi label="总运行" value={rows.length} note="全部历史运行" />
        <Kpi
          label="进行中"
          value={activeCount}
          tone={activeCount ? "info" : undefined}
          note="排队 / 执行 / 暂停"
        />
        <Kpi label="已完成" value={doneCount} tone="ok" note="成功与结束" />
        <Kpi
          label="失败"
          value={failedCount}
          tone={failedCount ? "danger" : undefined}
          note="失败或取消"
        />
        <Kpi label="事件总数" value={totalEvents} tone="info" note="跨全部运行" />
      </div>
      {workers && workers.status !== "online" ? (
        <Notice tone="warn">
          Worker 当前 {workers.status}，任务可能无法立即执行，请到诊断页检查。
        </Notice>
      ) : null}
      <div className="toolbar">
        <Search className="" style={{ width: 15, height: 15, color: "var(--muted)" }} />
        <input
          type="text"
          aria-label="搜索 run / mission id"
          placeholder="搜索 run / mission id"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <Filter className="" style={{ width: 15, height: 15, color: "var(--muted)" }} />
        <select
          value={projectFilter}
          onChange={(event) => setProjectFilter(event.target.value)}
          aria-label="项目过滤"
          style={{ flex: "0 0 auto" }}
        >
          <option value="">全部项目</option>
          {(projects.data ?? []).map((project) => (
            <option key={project.project_id} value={project.project_id}>
              {project.name} ({project.project_id.slice(0, 10)})
            </option>
          ))}
        </select>
        <select
          value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value)}
          aria-label="状态过滤"
          style={{ flex: "0 0 auto" }}
        >
          <option value="all">全部状态</option>
          <option value="active">进行中</option>
          <option value="done">已完成</option>
          <option value="failed">失败 / 取消</option>
        </select>
        {query || statusFilter !== "all" || projectFilter ? (
          <button
            className="btn btn-sm"
            onClick={() => {
              setQuery("");
              setStatusFilter("all");
              setProjectFilter("");
            }}
          >
            <X className="" />
            清除筛选
          </button>
        ) : null}
      </div>
      <div className="split" style={{ gridTemplateColumns: "minmax(0, 1.6fr) minmax(300px, 380px)" }}>
        <div>
          {rows.length === 0 ? (
            <EmptyState
              title="还没有运行记录"
              description="使用上方快速启动，或进入高级新建任务。"
              action={
                <button className="btn btn-primary" onClick={() => navigate("/setup")}>
                  <PlusCircle className="" />
                  新建任务
                </button>
              }
            />
          ) : filtered.length === 0 ? (
            <EmptyState title="没有匹配的运行" description="调整过滤条件后重试。" />
          ) : (
            <div className="card-grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))" }}>
              {filtered.map((run) => (
                <div
                  className="card"
                  key={run.run_id}
                  onClick={() => {
                    navigate("/cockpit");
                    setSelectedRunId(run.run_id);
                  }}
                  style={{ cursor: "pointer" }}
                >
                  <div className="panel-head" style={{ marginBottom: 6 }}>
                    <div className="card-title" style={{ margin: 0 }}>
                      <code>{run.run_id.slice(0, 18)}</code>
                    </div>
                    <Badge value={run.status}>{run.status}</Badge>
                  </div>
                  <p className="card-meta" style={{ marginBottom: 8 }}>
                    {missionByRun.get(run.run_id)?.name ?? "mission"}{" "}
                    <code>{run.mission_id.slice(0, 12)}</code>
                  </p>
                  {missionByRun.get(run.run_id)?.spec?.mission ? (
                    <p
                      className="muted"
                      style={{
                        fontSize: 12,
                        marginBottom: 8,
                        display: "-webkit-box",
                        WebkitLineClamp: 2,
                        WebkitBoxOrient: "vertical",
                        overflow: "hidden",
                      }}
                    >
                      {missionByRun.get(run.run_id)!.spec!.mission}
                    </p>
                  ) : null}
                  <div className="card-meta" style={{ display: "flex", gap: 12, marginBottom: 10 }}>
                    <span>{run.event_count} 事件</span>
                    <span>{run.source_run_id ? "fork" : "main"}</span>
                    <span>{run.created_at ?? "-"}</span>
                  </div>
                  <div className="btn-group">
                    <button
                      className="btn btn-sm"
                      onClick={(event) => {
                        event.stopPropagation();
                        navigate("/cockpit");
                        setSelectedRunId(run.run_id);
                      }}
                    >
                      <ArrowUpRight className="" />
                      控制台
                    </button>
                    <button
                      className="btn btn-sm"
                      onClick={(event) => {
                        event.stopPropagation();
                        navigate("/evidence");
                        setSelectedRunId(run.run_id);
                      }}
                      title="查看证据、发现与审批"
                    >
                      <ShieldCheck className="" />
                      证据
                    </button>
                    {run.status === "succeeded" ? (
                      <a
                        className="btn btn-sm"
                        href={`${CONTROL_URL}/api/v1/runs/${run.run_id}/report-bundle`}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(event) => event.stopPropagation()}
                      >
                        <Download className="" />
                        报告
                      </a>
                    ) : null}
                    {["running", "requested", "claimed"].includes(run.status) ? (
                      <button
                        className="btn btn-sm"
                        title="暂停运行"
                        onClick={(event) => {
                          event.stopPropagation();
                          runCommand.mutate({
                            runId: run.run_id,
                            action: "pause",
                          });
                        }}
                      >
                        <CirclePause className="" />
                        暂停
                      </button>
                    ) : null}
                    {run.status === "paused" ? (
                      <button
                        className="btn btn-sm"
                        title="恢复运行"
                        onClick={(event) => {
                          event.stopPropagation();
                          runCommand.mutate({
                            runId: run.run_id,
                            action: "resume",
                          });
                        }}
                      >
                        <CirclePlay className="" />
                        恢复
                      </button>
                    ) : null}
                    {["running", "requested", "claimed", "paused"].includes(run.status) ? (
                      <button
                        className="btn btn-sm btn-danger"
                        title="取消运行"
                        onClick={(event) => {
                          event.stopPropagation();
                          if (
                            window.confirm(
                              `确定取消运行 ${run.run_id.slice(0, 16)}？`,
                            )
                          ) {
                            runCommand.mutate({
                              runId: run.run_id,
                              action: "cancel",
                            });
                          }
                        }}
                      >
                        <CircleStop className="" />
                        取消
                      </button>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="stack">
          <Panel
            title="实时活动"
            icon={Activity}
            actions={
              latestRunId ? (
                <>
                  <span className="muted" style={{ fontSize: 12 }}>
                    {latestRunId.slice(0, 12)}
                  </span>
                  {latestEvents.dataUpdatedAt ? (
                    <span className="muted" style={{ fontSize: 11 }}>
                      更新{" "}
                      {new Date(latestEvents.dataUpdatedAt).toLocaleTimeString()}
                    </span>
                  ) : null}
                </>
              ) : null
            }
          >
            {!latestRunId ? (
              <EmptyState title="暂无活动" description="启动任务后这里会实时滚动。" />
            ) : latestEvents.isLoading ? (
              <Loading label="加载活动" />
            ) : (
              <div className="activity-feed">
                {(latestEvents.data ?? []).slice(-12).reverse().map((event) => (
                  <div className="activity-item" key={event.event_id}>
                    <div className="activity-dot info">
                      <Activity className="" />
                    </div>
                    <div className="activity-body">
                      <div className="activity-head">
                        <span className="activity-title">{event.event_type}</span>
                        <span className="activity-time">#{event.sequence}</span>
                      </div>
                      <div className="activity-detail">
                        <code>{JSON.stringify(event.payload).slice(0, 90)}</code>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>
          <Panel title="环境状态" icon={Radar}>
            <div className="stack" style={{ gap: 8 }}>
              <div className="card">
                <div className="panel-head" style={{ marginBottom: 2 }}>
                  <div className="card-title" style={{ margin: 0 }}>
                    Worker
                  </div>
                  <Badge value={workers?.status ?? "unknown"}>
                    {workers?.status ?? "unknown"}
                  </Badge>
                </div>
                <p className="card-meta" style={{ margin: 0 }}>
                  {workers?.status === "online" ? (
                    <LiveDot tone="ok" />
                  ) : (
                    <LiveDot tone="warn" pulse={false} />
                  )}{" "}
                  Agent 执行单元
                </p>
              </div>
              {providers.map((provider) => (
                <div className="card" key={provider.provider_id}>
                  <div className="panel-head" style={{ marginBottom: 2 }}>
                    <div className="card-title" style={{ margin: 0 }}>
                      <Sparkles className="" style={{ width: 14, height: 14, color: "var(--violet)" }} />
                      {provider.provider_id}
                    </div>
                    <Badge value={provider.status}>{provider.status}</Badge>
                  </div>
                  <p className="card-meta" style={{ margin: 0 }}>
                    {provider.model}
                  </p>
                </div>
              ))}
            </div>
          </Panel>
        </div>
      </div>
      {runs.isLoading && rows.length === 0 ? <Loading label="加载运行" /> : null}
      {runs.isError ? <ErrorBanner message={String(runs.error)} /> : null}
    </section>
  );
}
