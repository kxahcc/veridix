import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ArrowUpRight, FileSearch, RefreshCw, Save } from "lucide-react";
import { control } from "../api.js";
import { ErrorBanner, Loading } from "../components/Status.js";
import { useRunSelection } from "../store.js";
import { Badge, EmptyState, Kpi, Notice, Panel } from "../components/ui.js";

type VulnRow = {
  finding_id: string;
  run_id: string;
  project_id?: string;
  vuln_category: string;
  endpoint: string;
  severity?: string;
  cvss_vector?: string;
  cvss_score?: number;
  status: string;
  remediation?: string;
  asset_id?: string;
  notes?: string;
};

export function VulnerabilitiesPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const setSelectedRunId = useRunSelection((state) => state.setSelectedRunId);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [severityFilter, setSeverityFilter] = useState("all");
  const [projectFilter, setProjectFilter] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editSeverity, setEditSeverity] = useState("medium");
  const [editRemediation, setEditRemediation] = useState("");
  const [editAssetId, setEditAssetId] = useState("");
  const [editCvss, setEditCvss] = useState("");
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => control.listProjects(),
  });
  const vulnerabilities = useQuery({
    queryKey: ["vulnerabilities", projectFilter],
    queryFn: () =>
      control.listVulnerabilities(
        projectFilter ? { project_id: projectFilter } : undefined,
      ),
    refetchInterval: 4000,
  });
  const risk = useQuery({
    queryKey: ["risk", projectFilter],
    queryFn: () => control.riskSummary(projectFilter || undefined),
    refetchInterval: 8000,
  });
  const update = useMutation({
    mutationFn: () =>
      control.updateVulnerability(editingId!, {
        severity: editSeverity,
        remediation: editRemediation,
        asset_id: editAssetId || undefined,
        cvss_vector: editCvss || undefined,
      }),
    onSuccess: () => {
      setEditingId(null);
      void queryClient.invalidateQueries({ queryKey: ["vulnerabilities"] });
      void queryClient.invalidateQueries({ queryKey: ["risk"] });
    },
  });
  const rows = (vulnerabilities.data ?? []) as VulnRow[];
  const filtered = useMemo(
    () =>
      rows.filter((row) => {
        if (statusFilter !== "all" && row.status !== statusFilter) {
          return false;
        }
        if (severityFilter !== "all" && row.severity !== severityFilter) {
          return false;
        }
        const text = query.trim().toLowerCase();
        if (!text) {
          return true;
        }
        return (
          row.vuln_category.toLowerCase().includes(text) ||
          row.endpoint.toLowerCase().includes(text) ||
          row.finding_id.toLowerCase().includes(text)
        );
      }),
    [rows, query, statusFilter, severityFilter],
  );
  const riskData = (risk.data ?? {}) as {
    total_findings?: number;
    open_count?: number;
    risk_score?: number;
    severity_counts?: Record<string, number>;
  };
  const severityCounts = riskData.severity_counts ?? {};

  return (
    <section>
      <header className="page-head">
        <div className="page-head-copy">
          <p className="page-eyebrow">Vulnerability & Risk</p>
          <h1>漏洞与风险</h1>
          <p className="page-sub">
            聚合全部发现，按严重级别与状态管理漏洞，并查看风险评分。
          </p>
        </div>
        <div className="actions">
          <button
            className="btn"
            onClick={() => {
              void vulnerabilities.refetch();
              void risk.refetch();
            }}
          >
            <RefreshCw className="" />
            刷新
          </button>
        </div>
      </header>
      <div className="kpi-grid">
        <Kpi label="漏洞总数" value={riskData.total_findings ?? 0} note="全部 finding" />
        <Kpi
          label="未关闭"
          value={riskData.open_count ?? 0}
          tone={(riskData.open_count ?? 0) > 0 ? "warn" : undefined}
          note="待验证 / 已确认"
        />
        <Kpi
          label="风险评分"
          value={riskData.risk_score ?? 0}
          tone={Number(riskData.risk_score) >= 20 ? "danger" : "ok"}
          note="按严重度与状态加权"
        />
        <Kpi
          label="高危"
          value={(severityCounts.high ?? 0) + (severityCounts.critical ?? 0)}
          tone={(severityCounts.high ?? 0) + (severityCounts.critical ?? 0) ? "danger" : undefined}
          note={`critical ${severityCounts.critical ?? 0} / high ${severityCounts.high ?? 0}`}
        />
      </div>
      <div className="toolbar">
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
        <input
          type="text"
          aria-label="搜索漏洞"
          placeholder="搜索类别 / endpoint / finding id"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <select
          value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value)}
          aria-label="漏洞状态过滤"
          style={{ flex: "0 0 auto" }}
        >
          <option value="all">全部状态</option>
          <option value="candidate">candidate</option>
          <option value="supported">supported</option>
          <option value="verified">verified</option>
          <option value="open">open</option>
          <option value="fixed">fixed</option>
          <option value="rejected">rejected</option>
          <option value="accepted_risk">accepted_risk</option>
        </select>
        <select
          value={severityFilter}
          onChange={(event) => setSeverityFilter(event.target.value)}
          aria-label="严重级别过滤"
          style={{ flex: "0 0 auto" }}
        >
          <option value="all">全部级别</option>
          <option value="critical">critical</option>
          <option value="high">high</option>
          <option value="medium">medium</option>
          <option value="low">low</option>
          <option value="info">info</option>
        </select>
      </div>
      <Panel
        title="漏洞列表"
        icon={FileSearch}
        actions={<span className="muted" style={{ fontSize: 12 }}>{filtered.length} 条</span>}
      >
        {vulnerabilities.isLoading ? (
          <Loading label="加载漏洞" />
        ) : vulnerabilities.isError ? (
          <ErrorBanner message={String(vulnerabilities.error)} />
        ) : rows.length === 0 ? (
          <EmptyState
            title="暂无漏洞"
            description="Agent 提交 finding 后会出现在这里，并参与风险评分。"
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
          <EmptyState title="没有匹配的漏洞" description="调整过滤条件后重试。" />
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Finding</th>
                  <th>类别</th>
                  <th>Endpoint</th>
                    <th>严重度</th>
                    <th>CVSS</th>
                    <th>状态</th>
                  <th>项目</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((row) => (
                  <tr key={row.finding_id}>
                    <td className="mono">{row.finding_id.slice(0, 16)}</td>
                    <td>{row.vuln_category}</td>
                    <td className="mono">{row.endpoint}</td>
                    <td>
                      <Badge value={row.severity ?? "medium"}>
                        {row.severity ?? "medium"}
                      </Badge>
                    </td>
                    <td className="num" title={row.cvss_vector || undefined}>
                      {row.cvss_score ? String(row.cvss_score) : "-"}
                    </td>
                    <td>
                      <Badge value={row.status}>{row.status}</Badge>
                    </td>
                    <td className="mono">{row.project_id?.slice(0, 14) ?? "-"}</td>
                    <td>
                      <div className="btn-group">
                        <button
                          className="btn btn-sm"
                          onClick={() => {
                            setEditingId(row.finding_id);
                            setEditSeverity(row.severity ?? "medium");
                            setEditRemediation(row.remediation ?? "");
                            setEditAssetId(row.asset_id ?? "");
                            setEditCvss(row.cvss_vector ?? "");
                          }}
                        >
                          编辑
                        </button>
                        {row.run_id ? (
                          <button
                            className="btn btn-sm"
                            onClick={() => {
                              navigate("/evidence");
                              setSelectedRunId(row.run_id);
                            }}
                          >
                            <ArrowUpRight className="" />
                            证据
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {editingId ? (
          <div className="panel" style={{ marginTop: 12, marginBottom: 0 }}>
            <div className="form-grid">
              <label className="field">
                严重度
                <select
                  value={editSeverity}
                  onChange={(event) => setEditSeverity(event.target.value)}
                >
                  <option value="critical">critical</option>
                  <option value="high">high</option>
                  <option value="medium">medium</option>
                  <option value="low">low</option>
                  <option value="info">info</option>
                </select>
              </label>
              <label className="field">
                关联资产 id（可选）
                <input
                  value={editAssetId}
                  onChange={(event) => setEditAssetId(event.target.value)}
                  placeholder="asset_..."
                />
              </label>
              <label className="field">
                CVSS 向量
                <input
                  value={editCvss}
                  onChange={(event) => setEditCvss(event.target.value)}
                  placeholder="CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H"
                />
              </label>
              <label className="field" style={{ gridColumn: "1 / -1" }}>
                修复建议
                <textarea
                  rows={2}
                  value={editRemediation}
                  onChange={(event) => setEditRemediation(event.target.value)}
                />
              </label>
            </div>
            <div className="btn-group" style={{ marginTop: 10 }}>
              <button
                className="btn btn-primary"
                onClick={() => update.mutate()}
                disabled={update.isPending}
              >
                <Save className="" />
                保存
              </button>
              <button className="btn" onClick={() => setEditingId(null)}>
                取消
              </button>
            </div>
          </div>
        ) : null}
        {update.isError ? (
          <Notice tone="error">{String(update.error)}</Notice>
        ) : null}
      </Panel>
      {risk.isError ? <ErrorBanner message={String(risk.error)} /> : null}
    </section>
  );
}
