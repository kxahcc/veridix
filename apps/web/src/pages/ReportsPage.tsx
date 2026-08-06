import { useQuery } from "@tanstack/react-query";
import { Download, FileText, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CONTROL_URL, control } from "../api.js";
import { ErrorBanner, Loading } from "../components/Status.js";
import { Badge, EmptyState, Kpi, Panel } from "../components/ui.js";
import { MarkdownView } from "../components/MarkdownView.js";

type SummaryRow = {
  run_id: string;
  mission_id: string;
  status: string;
  created_at?: string;
  findings: number;
  verified: number;
  gate_pass: boolean;
  sources?: Record<string, number>;
};

export function ReportsPage() {
  const navigate = useNavigate();
  const [previewRun, setPreviewRun] = useState<string | null>(null);
  const previewRef = useRef<HTMLDivElement | null>(null);
  const [statusFilter, setStatusFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [limit, setLimit] = useState(25);
  const summary = useQuery({
    queryKey: ["reports-summary"],
    queryFn: () => control.requestPublic("/api/v1/reports/summary"),
  });
  const allRuns = (
    (summary.data as { rows?: SummaryRow[] } | undefined)?.rows ?? []
  ).slice(0, 100);
  const runRows = allRuns.filter((run) => {
    if (statusFilter !== "all" && run.status !== statusFilter) {
      return false;
    }
    const text = search.trim().toLowerCase();
    if (
      text &&
      !`${run.run_id} ${run.mission_id}`.toLowerCase().includes(text)
    ) {
      return false;
    }
    return true;
  });
  const visibleRuns = runRows.slice(0, limit);
  const preview = useQuery({
    queryKey: ["report-preview", previewRun],
    queryFn: async () => {
      const response = await fetch(
        `${CONTROL_URL}/api/v1/runs/${previewRun}/report`,
      );
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      return response.text();
    },
    enabled: Boolean(previewRun),
  });

  useEffect(() => {
    if (previewRun) {
      previewRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    }
  }, [previewRun, preview.isLoading, preview.isSuccess, preview.isError]);

  return (
    <section>
      <header className="page-head">
        <div className="page-head-copy">
          <p className="page-eyebrow">Reports</p>
          <h1>报告</h1>
          <p className="page-sub">
            带发现的运行与可下载的报告归档包。
          </p>
        </div>
        <div className="actions">
          <button className="btn" onClick={() => void summary.refetch()}>
            <RefreshCw className="" />
            刷新
          </button>
        </div>
      </header>
      {summary.isSuccess ? (
        <div className="kpi-grid">
          <Kpi label="运行" value={runRows.length} note={`共 ${allRuns.length} 条`} />
          <Kpi
            label="发现"
            value={visibleRuns.reduce(
              (sum, row) => sum + (row.findings ?? 0),
              0,
            )}
            tone="info"
            note="全部已拉取"
          />
          <Kpi
            label="已核实"
            value={visibleRuns.reduce(
              (sum, row) => sum + (row.verified ?? 0),
              0,
            )}
            tone="ok"
            note="verified"
          />
        </div>
      ) : null}
      {summary.isSuccess ? (
        <div className="toolbar" style={{ marginBottom: 12 }}>
          <input
            type="text"
            aria-label="搜索 run / mission id"
            placeholder="搜索 run / mission id"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <select
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
            aria-label="状态过滤"
          >
            <option value="all">全部状态</option>
            <option value="queued">queued</option>
            <option value="running">running</option>
            <option value="paused">paused</option>
            <option value="succeeded">succeeded</option>
            <option value="failed">failed</option>
            <option value="cancelled">cancelled</option>
          </select>
        </div>
      ) : null}
      {summary.isLoading && <Loading label="Loading reports" />}
      {summary.isError && <ErrorBanner message={String(summary.error)} />}
      {summary.isSuccess && runRows.length === 0 && (
        <EmptyState
          title="暂无运行"
          description="创建并启动任务后生成报告。"
          action={
            <button
              className="btn btn-sm"
              onClick={() => navigate("/setup")}
            >
              新建任务
            </button>
          }
        />
      )}
      {runRows.length > 0 && (
        <Panel title="报告列表" icon={FileText}>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Mission</th>
                  <th>状态</th>
                  <th>发现</th>
                  <th>已核实</th>
                  <th>报告</th>
                </tr>
              </thead>
              <tbody>
                {visibleRuns.map((run) => {
                  const rows = run.findings ?? 0;
                  const verified = run.verified ?? 0;
                  const gate = run.gate_pass;
                  const sources = Object.entries(run.sources ?? {});
                  return (
                    <tr key={run.run_id}>
                      <td>
                        <code>{run.run_id.slice(0, 18)}</code>
                      </td>
                      <td>
                        <code>{run.mission_id.slice(0, 18)}</code>
                      </td>
                      <td>
                        <Badge value={run.status}>{run.status}</Badge>
                      </td>
                      <td className="num">{rows}</td>
                      <td className="num">{verified}</td>
                      <td>
                        <div className="stack" style={{ gap: 6 }}>
                          <div>
                            {sources.map(([source, count]) => (
                              <Badge key={source} value={source}>
                                {source} {count}
                              </Badge>
                            ))}
                          </div>
                          <div>
                            {run.status === "succeeded" || rows > 0 ? (
                              <>
                                <a
                                  className="btn btn-sm btn-primary"
                                  href={`${CONTROL_URL}/api/v1/runs/${run.run_id}/report-bundle`}
                                  target="_blank"
                                  rel="noreferrer"
                                >
                                  <Download className="" />
                                  下载报告
                                </a>
                                <button
                                  className="btn btn-sm"
                                  onClick={() => setPreviewRun(run.run_id)}
                                >
                                  预览
                                </button>
                                <a
                                  className="btn btn-sm"
                                  href={`${CONTROL_URL}/api/v1/runs/${run.run_id}/report.html`}
                                  target="_blank"
                                  rel="noreferrer"
                                >
                                  HTML 报告
                                </a>
                              </>
                            ) : (
                              <span className="muted">pending</span>
                            )}
                            {typeof gate === "boolean" ? (
                              <span
                                className="muted"
                                style={{ marginLeft: 8, fontSize: 12 }}
                              >
                                证据门禁:{" "}
                                {gate ? (
                                  <Badge value="passed">通过</Badge>
                                ) : (
                                  <Badge value="failed">未通过</Badge>
                                )}{" "}
                                · verified {verified}
                              </span>
                            ) : null}
                          </div>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
      {runRows.length > limit ? (
        <div className="actions" style={{ marginTop: 12 }}>
          <button
            className="btn"
            onClick={() => setLimit((current) => current + 25)}
          >
            加载更多
          </button>
        </div>
      ) : null}
      {previewRun ? (
        <div ref={previewRef}>
          <Panel
            title="报告预览"
            icon={FileText}
            actions={
              <>
                <a
                  className="btn btn-sm"
                  href={`${CONTROL_URL}/api/v1/runs/${previewRun}/report`}
                  download
                >
                  下载 Markdown
                </a>
                <button
                  className="btn btn-sm"
                  onClick={() => setPreviewRun(null)}
                >
                  关闭
                </button>
              </>
            }
          >
            {preview.isLoading ? (
              <Loading label="加载报告预览" />
            ) : preview.isError ? (
              <ErrorBanner message={String(preview.error)} />
            ) : (
              <div
                className="report-preview"
                style={{ maxHeight: 480, overflow: "auto" }}
              >
                <MarkdownView
                  value={String(preview.data ?? "")}
                  testId="report-preview-body"
                />
              </div>
            )}
          </Panel>
        </div>
      ) : null}
    </section>
  );
}
