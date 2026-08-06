import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Filter, RefreshCw, Search, ShieldAlert } from "lucide-react";
import { control, CONTROL_URL } from "../api.js";
import { Empty, Loading } from "../components/Status.js";
import { useRunSelection } from "../store.js";
import type { WebObservation } from "@veridix/sdk-typescript";
import { Badge, EmptyState, Kpi, Notice, Panel } from "../components/ui.js";
import { RunPicker } from "../components/RunPicker.js";

export function WebConsole() {
  const navigate = useNavigate();
  const runId = useRunSelection((state) => state.selectedRunId);
  const [methodFilter, setMethodFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [query, setQuery] = useState("");
  const observations = useQuery({
    queryKey: ["web-observations", runId],
    queryFn: () => control.getWebObservations(runId!),
    enabled: Boolean(runId),
    refetchInterval: 2000,
  });
  const rows = observations.data ?? [];
  const filtered = useMemo(() => {
    const text = query.trim().toLowerCase();
    return rows.filter((observation) => {
      if (methodFilter !== "all" && observation.method !== methodFilter) {
        return false;
      }
      if (
        statusFilter === "ok" &&
        (observation.status_code < 200 || observation.status_code >= 400)
      ) {
        return false;
      }
      if (
        statusFilter === "error" &&
        observation.status_code < 400
      ) {
        return false;
      }
      if (
        statusFilter === "redirect" &&
        (observation.status_code < 300 || observation.status_code >= 400)
      ) {
        return false;
      }
      if (text) {
        return (
          observation.url.toLowerCase().includes(text) ||
          observation.endpoint.toLowerCase().includes(text)
        );
      }
      return true;
    });
  }, [rows, methodFilter, statusFilter, query]);
  const errorCount = rows.filter((observation) => observation.status_code >= 400).length;
  const redactedCount = rows.filter((observation) => observation.redacted).length;
  const methods = useMemo(
    () => Array.from(new Set(rows.map((observation) => observation.method))),
    [rows],
  );

  if (!runId) {
    return (
      <section>
        <header className="page-head">
          <div className="page-head-copy">
            <p className="page-eyebrow">Proxy Traffic</p>
            <h1>Web 流量</h1>
            <p className="page-sub">
              选择一个运行以查看代理捕获的请求与响应。
            </p>
          </div>
        </header>
        <RunPicker />
      </section>
    );
  }
  if (observations.isLoading) {
    return <Loading label="Loading observations" />;
  }
  return (
    <section>
      <header className="page-head">
        <div className="page-head-copy">
          <p className="page-eyebrow">Proxy Traffic</p>
          <h1>Web 流量</h1>
          <p className="page-sub">
            当前运行 <code>{runId.slice(0, 18)}</code>
          </p>
        </div>
        <div className="actions">
          <button
            className="btn"
            onClick={() => void observations.refetch()}
          >
            <RefreshCw className="" />
            刷新
          </button>
          <a
            className="link-button"
            href={`${CONTROL_URL}/api/v1/runs/${runId}/report-bundle`}
            target="_blank"
            rel="noreferrer"
          >
            <ShieldAlert className="" />
            报告包
          </a>
        </div>
      </header>
      <div className="kpi-grid">
        <Kpi label="观测总数" value={rows.length} note="代理捕获" />
        <Kpi
          label="错误响应"
          value={errorCount}
          tone={errorCount ? "danger" : undefined}
          note="HTTP >= 400"
        />
        <Kpi
          label="已脱敏"
          value={redactedCount}
          tone={redactedCount ? "warn" : undefined}
          note="敏感字段替换"
        />
        <Kpi label="方法数" value={methods.length} tone="info" note={methods.join(" / ")} />
      </div>
      <div className="toolbar">
        <Search className="" style={{ width: 14, height: 14, color: "var(--muted)" }} />
        <input
          type="text"
          aria-label="搜索 URL / endpoint"
          placeholder="搜索 URL / endpoint"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <Filter className="" style={{ width: 14, height: 14, color: "var(--muted)" }} />
        <select
          value={methodFilter}
          onChange={(event) => setMethodFilter(event.target.value)}
          aria-label="方法过滤"
        >
          <option value="all">全部方法</option>
          {methods.map((method) => (
            <option key={method} value={method}>
              {method}
            </option>
          ))}
        </select>
        <select
          value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value)}
          aria-label="状态过滤"
        >
          <option value="all">全部状态</option>
          <option value="ok">2xx</option>
          <option value="redirect">3xx</option>
          <option value="error">4xx / 5xx</option>
        </select>
      </div>
      {rows.length === 0 ? (
        <EmptyState
          title="暂无 Web 观测"
          description="运行通过代理访问目标后，请求与响应会出现在这里。"
          action={
            <button
              className="btn btn-sm"
              onClick={() => navigate("/setup")}
            >
              新建任务
            </button>
          }
        />
      ) : filtered.length === 0 ? (
        <EmptyState title="没有匹配的观测" description="调整过滤条件后重试。" />
      ) : (
        <div className="observation-list">
          {filtered.map((observation: WebObservation) => (
            <div key={observation.request_id} className="observation-card">
              <div className="observation-head">
                <Badge className="badge-method">{observation.method}</Badge>
                <span className="observation-url">{observation.url}</span>
                <Badge className={`badge-${observation.status_code}`}>
                  {observation.status_code}
                </Badge>
                {observation.redacted ? (
                  <Badge className="badge-warn">redacted</Badge>
                ) : null}
                {observation.truncated ? (
                  <Badge className="badge-paused">truncated</Badge>
                ) : null}
              </div>
              <div className="observation-meta">
                <span>{observation.request_size} B 请求</span>
                <span>{observation.response_size} B 响应</span>
                <span>{observation.content_type || "无 Content-Type"}</span>
                <span>session {observation.web_session_id.slice(0, 10)}</span>
              </div>
              <details>
                <summary>请求头</summary>
                <pre>{JSON.stringify(observation.request_headers, null, 2)}</pre>
              </details>
              <details>
                <summary>响应头</summary>
                <pre>{JSON.stringify(observation.response_headers, null, 2)}</pre>
              </details>
              <details>
                <summary>请求</summary>
                <pre>{observation.request_body || "(empty)"}</pre>
              </details>
              <details open>
                <summary>响应</summary>
                <pre>{observation.response_body || "(empty)"}</pre>
              </details>
              {observation.replay_proof && (
                <details>
                  <summary>重放证明</summary>
                  <pre>{JSON.stringify(observation.replay_proof, null, 2)}</pre>
                </details>
              )}
            </div>
          ))}
        </div>
      )}
      {observations.isError ? (
        <Notice tone="error">{String(observations.error)}</Notice>
      ) : null}
    </section>
  );
}
