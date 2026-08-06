import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Download,
  FileSearch,
  Fingerprint,
  GitMerge,
  KeyRound,
  RefreshCw,
  ShieldCheck,
  ShieldX,
} from "lucide-react";
import { control, CONTROL_URL } from "../api.js";
import { Empty, ErrorBanner, Loading } from "../components/Status.js";
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
import { AttackGraphPanel } from "./AttackGraphPanel.js";
import type { Finding, MergedFinding } from "@veridix/sdk-typescript";

type TabId =
  | "findings"
  | "gates"
  | "evidence"
  | "merged"
  | "approvals"
  | "graph";

const TABS: Array<{ id: TabId; label: string; icon: typeof FileSearch }> = [
  { id: "findings", label: "发现", icon: FileSearch },
  { id: "gates", label: "人工门禁", icon: KeyRound },
  { id: "evidence", label: "证据", icon: ShieldCheck },
  { id: "merged", label: "去重组", icon: GitMerge },
  { id: "approvals", label: "审批", icon: KeyRound },
  { id: "graph", label: "攻击图", icon: Fingerprint },
];

function artifactIdFromRef(ref: string): string | null {
  const match = ref.match(/artifact:\/\/sha256\/([0-9a-f]{64})/);
  return match ? match[1] : null;
}

export function EvidenceDetail() {
  const navigate = useNavigate();
  const runId = useRunSelection((state) => state.selectedRunId);
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<TabId>("findings");
  const [artifactText, setArtifactText] = useState<string | null>(null);
  const [findingQuery, setFindingQuery] = useState("");
  const [findingStatus, setFindingStatus] = useState("all");
  const approvals = useQuery({
    queryKey: ["approvals", runId],
    queryFn: () => control.listApprovals(runId!),
    enabled: Boolean(runId),
    refetchInterval: 3000,
  });
  const findings = useQuery({
    queryKey: ["findings", runId],
    queryFn: () => control.listFindings(runId!),
    enabled: Boolean(runId),
    refetchInterval: 3000,
  });
  const merged = useQuery({
    queryKey: ["findings-merged", runId],
    queryFn: () => control.listMergedFindings(runId!),
    enabled: Boolean(runId),
    refetchInterval: 3000,
  });
  const evidence = useQuery({
    queryKey: ["evidence", runId],
    queryFn: () =>
      control.requestPublic(`/api/v1/runs/${runId}/evidence`),
    enabled: Boolean(runId),
    refetchInterval: 3000,
  });
  const humanGates = useQuery({
    queryKey: ["human-gates", runId],
    queryFn: () =>
      control.requestPublic(`/api/v1/runs/${runId}/human-gates`),
    enabled: Boolean(runId),
    refetchInterval: 3000,
  });
  const decide = useMutation({
    mutationFn: (args: { id: string; approved: boolean }) =>
      control.decideApproval(args.id, args.approved, "web-operator"),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["approvals", runId] });
    },
  });
  const review = useMutation({
    mutationFn: (args: { id: string; decision: string }) =>
      control.reviewFinding(args.id, args.decision, "web-operator"),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["findings", runId] });
    },
  });
  const retest = useMutation({
    mutationFn: (id: string) =>
      control.retestFinding(id, { matched: true, replayed_status: 200 }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["findings", runId] });
    },
  });
  const resolveGate = useMutation({
    mutationFn: (args: { nodeId: string; approved: boolean }) =>
      fetch(
        `${CONTROL_URL}/api/v1/runs/${runId}/human-gates/${args.nodeId}/resolve`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            approved: args.approved,
            reason: args.approved ? "approved-by-web" : "rejected-by-web",
          }),
        },
      ).then((response) => {
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        return response.json();
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["human-gates", runId],
      });
    },
  });

  if (!runId) {
    return (
      <section>
        <header className="page-head">
          <div className="page-head-copy">
            <p className="page-eyebrow">Evidence Review</p>
            <h1>证据与发现</h1>
            <p className="page-sub">
              选择一个运行以审查发现、证据、审批和攻击图。
            </p>
          </div>
        </header>
        <RunPicker />
      </section>
    );
  }
  if (approvals.isLoading) {
    return <Loading label="Loading evidence" />;
  }
  if (approvals.isError) {
    return <ErrorBanner message={String(approvals.error)} />;
  }
  const rows = approvals.data ?? [];
  const evidenceRows = evidence.data as
    | Array<Record<string, unknown>>
    | undefined;
  const findingRows = findings.data ?? [];
  const filteredFindings = findingRows.filter((finding) => {
    const text = findingQuery.trim().toLowerCase();
    if (findingStatus !== "all" && finding.status !== findingStatus) {
      return false;
    }
    if (!text) {
      return true;
    }
    return (
      finding.vuln_category.toLowerCase().includes(text) ||
      finding.endpoint.toLowerCase().includes(text) ||
      finding.finding_id.toLowerCase().includes(text)
    );
  });
  const pendingGates = (
    (humanGates.data as
      | { pending?: Array<{ node_id: string; prompt: string }> }
      | undefined)?.pending ?? []
  );
  const verifiedCount = findingRows.filter(
    (finding) => finding.status === "verified",
  ).length;
  const supportedCount = findingRows.filter(
    (finding) => finding.status === "supported",
  ).length;
  const pendingApprovals = rows.filter(
    (approval) => approval.state === "requested",
  ).length;

  return (
    <section>
      <header className="page-head">
        <div className="page-head-copy">
          <p className="page-eyebrow">Evidence Review</p>
          <h1>证据与发现</h1>
          <p className="page-sub">
            当前运行 <code>{runId.slice(0, 18)}</code>
          </p>
          <SyncStamp dataUpdatedAt={findings.dataUpdatedAt} />
        </div>
        <div className="actions">
          <button
            className="btn"
            onClick={() => {
              void queryClient.invalidateQueries();
            }}
          >
            <RefreshCw className="" />
            刷新
          </button>
          <a
            className="link-button"
            href={`${CONTROL_URL}/api/v1/runs/${runId}/report-bundle`}
            download
          >
            <Download className="" />
            下载报告
          </a>
        </div>
      </header>
      <div className="kpi-grid">
        <Kpi label="发现总数" value={findingRows.length} note="全部 finding" />
        <Kpi
          label="已核实"
          value={verifiedCount + supportedCount}
          tone="ok"
          note={`verified ${verifiedCount} / supported ${supportedCount}`}
        />
        <Kpi
          label="待审批"
          value={pendingApprovals}
          tone={pendingApprovals ? "warn" : undefined}
          note="工具风险审批"
        />
        <Kpi
          label="人工门禁"
          value={pendingGates.length}
          tone={pendingGates.length ? "warn" : undefined}
          note="等待人工决定"
        />
        <Kpi
          label="证据记录"
          value={evidenceRows?.length ?? 0}
          tone="info"
          note="已物化证据"
        />
      </div>
      <div className="tabs">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            className={`tab${activeTab === tab.id ? " active" : ""}`}
            onClick={() => setActiveTab(tab.id)}
          >
            <tab.icon className="" />
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === "findings" ? (
        <Panel
          title="发现列表"
          icon={FileSearch}
          actions={<span className="muted" style={{ fontSize: 12 }}>{filteredFindings.length} 条</span>}
        >
          <div className="toolbar">
            <input
              type="text"
              aria-label="搜索发现"
              placeholder="搜索类别 / endpoint / finding id"
              value={findingQuery}
              onChange={(event) => setFindingQuery(event.target.value)}
            />
            <select
              value={findingStatus}
              onChange={(event) => setFindingStatus(event.target.value)}
              aria-label="发现状态过滤"
              style={{ flex: "0 0 auto" }}
            >
              <option value="all">全部状态</option>
              <option value="verified">verified</option>
              <option value="supported">supported</option>
              <option value="open">open</option>
              <option value="fixed">fixed</option>
              <option value="rejected">rejected</option>
            </select>
          </div>
          {findingRows.length === 0 ? (
            <EmptyState
              title="暂无发现"
              description="Agent 产生 finding 后会出现在这里。"
              action={
                <button
                  className="btn btn-sm"
                  onClick={() => navigate("/setup")}
                >
                  新建任务
                </button>
              }
            />
          ) : filteredFindings.length === 0 ? (
            <EmptyState title="没有匹配的发现" description="调整搜索或状态过滤后重试。" />
          ) : (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Finding</th>
                    <th>类别</th>
                    <th>Endpoint</th>
                    <th>参数</th>
                    <th>状态</th>
                    <th>Notes</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredFindings.map((finding: Finding) => (
                    <tr key={finding.finding_id}>
                      <td className="mono">{finding.finding_id.slice(0, 16)}</td>
                      <td>{finding.vuln_category}</td>
                      <td className="mono">{finding.endpoint}</td>
                      <td>{finding.param || "-"}</td>
                      <td>
                        <Badge value={finding.status}>{finding.status}</Badge>
                      </td>
                      <td className="muted" style={{ maxWidth: 280 }}>
                        {finding.notes || "-"}
                      </td>
                      <td>
                        <div className="btn-group">
                          {["verified", "supported"].includes(finding.status) ? (
                            <>
                              <button
                                className="btn btn-sm"
                                onClick={() =>
                                  review.mutate({
                                    id: finding.finding_id,
                                    decision: "open",
                                  })
                                }
                              >
                                <CheckCircle2 className="" />
                                打开
                              </button>
                              <button
                                className="btn btn-sm btn-danger"
                                onClick={() => {
                                  if (
                                    window.confirm(
                                      `确定拒绝发现 ${finding.finding_id.slice(0, 16)}？该操作会改变发现状态。`,
                                    )
                                  ) {
                                    review.mutate({
                                      id: finding.finding_id,
                                      decision: "rejected",
                                    });
                                  }
                                }}
                              >
                                <ShieldX className="" />
                                拒绝
                              </button>
                            </>
                          ) : null}
                          {["verified", "open", "fixed"].includes(finding.status) ? (
                            <button
                              className="btn btn-sm"
                              onClick={() => retest.mutate(finding.finding_id)}
                            >
                              <RefreshCw className="" />
                              重测
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
        </Panel>
      ) : null}

      {activeTab === "gates" ? (
        <Panel title="人工门禁" icon={KeyRound}>
          {pendingGates.length === 0 ? (
            <EmptyState title="没有待处理门禁" description="所有人工节点均已决定。" />
          ) : (
            <div className="stack" style={{ gap: 10 }}>
              {pendingGates.map((gate) => (
                <div className="card" key={gate.node_id}>
                  <div className="card-title">
                    <code>{gate.node_id}</code>
                  </div>
                  <p className="card-meta">{gate.prompt}</p>
                  <div className="btn-group">
                    <button
                      className="btn btn-sm btn-primary"
                      onClick={() =>
                        resolveGate.mutate({
                          nodeId: gate.node_id,
                          approved: true,
                        })
                      }
                    >
                      批准
                    </button>
                    <button
                      className="btn btn-sm btn-danger"
                      onClick={() => {
                        if (
                          window.confirm(
                            `确定拒绝人工门禁 ${gate.node_id}？`,
                          )
                        ) {
                          resolveGate.mutate({
                            nodeId: gate.node_id,
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
        </Panel>
      ) : null}

      {activeTab === "evidence" ? (
        <Panel
          title="证据记录"
          icon={ShieldCheck}
          actions={<span className="muted" style={{ fontSize: 12 }}>{(evidenceRows ?? []).length} 条</span>}
        >
          {(evidenceRows ?? []).length === 0 ? (
            <EmptyState title="暂无证据记录" description="验证器物化证据后显示。" />
          ) : (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Evidence</th>
                    <th>来源</th>
                    <th>动作</th>
                    <th>Artifacts</th>
                    <th>置信度</th>
                    <th>解析器</th>
                    <th>重放</th>
                  </tr>
                </thead>
                <tbody>
                  {(evidenceRows ?? []).map((item) => (
                    <tr key={String(item.evidence_id)}>
                      <td className="mono">{String(item.evidence_id).slice(0, 16)}</td>
                      <td>{String(item.source_type)}</td>
                      <td className="mono">{String(item.action_ref)}</td>
                      <td>
                        <div className="btn-group">
                          {((item.artifact_refs as string[]) ?? []).map((ref) => {
                            const artifactId = artifactIdFromRef(ref);
                            return artifactId ? (
                              <button
                                key={artifactId}
                                className="btn btn-sm"
                                onClick={async () => {
                                  const response = await fetch(
                                    `${CONTROL_URL}/api/v1/artifacts/${artifactId}?preview=true`,
                                  );
                                  const payload = (await response.json()) as {
                                    preview: string;
                                    truncated: boolean;
                                  };
                                  setArtifactText(
                                    payload.truncated
                                      ? `${payload.preview}\n...[truncated]`
                                      : payload.preview,
                                  );
                                }}
                              >
                                {artifactId.slice(0, 8)}
                              </button>
                            ) : (
                              <code key={ref}>{ref}</code>
                            );
                          })}
                        </div>
                      </td>
                      <td>{String(item.confidence)}</td>
                      <td>{String(item.parser_version)}</td>
                      <td>
                        {Object.keys(
                          (item.replay_proof as Record<string, unknown>) ?? {},
                        ).join(", ") || "none"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {artifactText !== null ? (
            <div style={{ marginTop: 12 }}>
              <Notice tone="info">Artifact 内容</Notice>
              <details className="result-box" open>
                <summary>Artifact content</summary>
                <pre>{artifactText}</pre>
              </details>
            </div>
          ) : null}
        </Panel>
      ) : null}

      {activeTab === "merged" ? (
        <Panel
          title="去重组"
          icon={GitMerge}
          actions={<span className="muted" style={{ fontSize: 12 }}>{(merged.data ?? []).length} 组</span>}
        >
          {(merged.data ?? []).length === 0 ? (
            <EmptyState title="暂无去重组" description="重复发现合并后显示。" />
          ) : (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>指纹</th>
                    <th>类别</th>
                    <th>Endpoint</th>
                    <th>证据</th>
                    <th>重复数</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  {(merged.data ?? []).map((view: MergedFinding) => (
                    <tr key={view.fingerprint}>
                      <td className="mono">{view.fingerprint.slice(0, 20)}</td>
                      <td>{view.vuln_category}</td>
                      <td className="mono">{view.endpoint}</td>
                      <td className="num">{view.evidence_ids.length}</td>
                      <td className="num">{view.duplicate_count}</td>
                      <td>
                        <Badge value={view.status}>{view.status}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      ) : null}

      {activeTab === "approvals" ? (
        <Panel
          title="审批队列"
          icon={KeyRound}
          actions={<span className="muted" style={{ fontSize: 12 }}>{rows.length} 条</span>}
        >
          {rows.length === 0 ? (
            <EmptyState title="暂无审批事件" description="高风险工具调用会生成审批请求。" />
          ) : (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Approval</th>
                    <th>工具</th>
                    <th>风险</th>
                    <th>状态</th>
                    <th>原因</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((approval) => (
                    <tr key={approval.approval_id}>
                      <td className="mono">{approval.approval_id.slice(0, 16)}</td>
                      <td className="mono">{approval.tool_ref}</td>
                      <td>
                        <Badge value={approval.risk_level}>{approval.risk_level}</Badge>
                      </td>
                      <td>
                        <Badge value={approval.state}>{approval.state}</Badge>
                      </td>
                      <td className="muted">{approval.reason}</td>
                      <td>
                        {approval.state === "requested" ? (
                          <div className="btn-group">
                            <button
                              className="btn btn-sm btn-primary"
                              onClick={() =>
                                decide.mutate({
                                  id: approval.approval_id,
                                  approved: true,
                                })
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
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      ) : null}

      {activeTab === "graph" ? (
        <Panel title="攻击图" icon={Fingerprint}>
          <AttackGraphPanel runId={runId} />
        </Panel>
      ) : null}
      {decide.isError ? <Notice tone="error">{String(decide.error)}</Notice> : null}
      {review.isError ? <Notice tone="error">{String(review.error)}</Notice> : null}
      {resolveGate.isError ? <Notice tone="error">{String(resolveGate.error)}</Notice> : null}
      {retest.isError ? <Notice tone="error">{String(retest.error)}</Notice> : null}
    </section>
  );
}
