import { useQuery } from "@tanstack/react-query";
import {
  Badge,
  EmptyState,
  Kpi,
  Notice,
  Panel,
  SyncStamp,
} from "../components/ui.js";
import { ErrorBanner, Loading } from "../components/Status.js";
import { control } from "../api.js";

type Row = Record<string, unknown>;

export function AcceptancePage() {
  const summary = useQuery({
    queryKey: ["acceptance"],
    queryFn: () => control.requestPublic("/api/v1/acceptance"),
    refetchInterval: 15000,
  });
  const data = summary.data as
    | {
        gates?: { rows?: Row[]; overall?: string; failed?: string[] };
        lab_gates?: { rows?: Row[]; overall?: string };
        rag?: { rows?: Row[]; builtin?: boolean };
        readiness?: {
          overall?: string;
          regression?: {
            python?: Row;
            typescript?: Row;
          };
        };
        tool_smoke?: { rows?: Row[] };
        profile_engineering?: {
          deterministic?: Row;
          real_preset?: Row;
          real_presets?: Record<string, Row>;
          external_fixture?: Row;
          preset_fixtures?: Row;
          preset_count?: number;
        };
        tool_matrix?: {
          tools?: Row[];
          total_verified?: number;
          assertion?: string;
        };
        mcp_real?: {
          servers?: Record<string, { status?: string }>;
          assertion?: string;
        };
        acceptance_all?: {
          overall?: string;
          steps?: Array<{ name?: string; status?: string }>;
        };
      }
    | undefined;

  const gateRows = data?.gates?.rows ?? [];
  const labGateRows = data?.lab_gates?.rows ?? [];
  const ragRows = data?.rag?.rows ?? [];
  const smokeRows = data?.tool_smoke?.rows ?? [];
  const toolMatrixTools = data?.tool_matrix?.tools ?? [];
  const mcpServers = data?.mcp_real?.servers ?? {};
  const acceptanceSteps = data?.acceptance_all?.steps ?? [];
  const profileEngineering = data?.profile_engineering;
  const deterministicProfile = profileEngineering?.deterministic ?? {};
  const realPreset = profileEngineering?.real_preset ?? {};
  const hostRecon = profileEngineering?.real_presets?.["host-recon"] ?? {};
  const externalFixture = profileEngineering?.external_fixture ?? {};
  const presetFixtures = profileEngineering?.preset_fixtures ?? {};
  const baselinePreset = realPreset.baseline as Row | undefined;
  const presetGroup = realPreset.preset as Row | undefined;
  const baselineMean = (baselinePreset?.mean ?? {}) as Row;
  const presetMean = (presetGroup?.mean ?? {}) as Row;
  const baselineStd = (baselinePreset?.std ?? {}) as Row;
  const presetStd = (presetGroup?.std ?? {}) as Row;
  const regression = data?.readiness?.regression ?? {};
  const pythonPassed = regression.python?.passed;
  const tsStatus = regression.typescript?.status;

  return (
    <section>
      <header className="page-head">
        <div className="page-head-copy">
          <p className="page-eyebrow">Acceptance</p>
          <h1>验收</h1>
          <p className="page-sub">
            真实工具链门禁、RAG 检索基准与 release readiness 的统一视图。
          </p>
          <SyncStamp dataUpdatedAt={summary.dataUpdatedAt} />
        </div>
      </header>
      <div className="kpi-grid">
        <Kpi
          label="真实门禁"
          value={String(data?.gates?.overall ?? "-")}
          tone={data?.gates?.overall === "passed" ? "ok" : undefined}
          note={`${gateRows.length} 个场景`}
        />
        <Kpi
          label="RAG 基准"
          value={data?.rag?.builtin ? "内置 308" : "未运行"}
          tone="info"
          note="Qdrant hybrid"
        />
        <Kpi
          label="Readiness"
          value={String(data?.readiness?.overall ?? "-")}
          tone={data?.readiness?.overall === "ready" ? "ok" : undefined}
          note={`Python ${pythonPassed ?? "?"}`}
        />
        <Kpi
          label="工具 smoke"
          value={smokeRows.length}
          tone="info"
          note="镜像内已验证"
        />
        <Kpi
          label="Profile 工程"
          value={profileEngineering?.preset_count ?? 0}
          tone="info"
          note={`${String(realPreset.preset_id ?? "-")} + ${String(
            hostRecon.preset_id ?? "-",
          )} 真实基准`}
        />
      </div>

      {summary.isLoading ? (
        <Loading label="加载验收摘要" />
      ) : summary.isError ? (
        <ErrorBanner message={String(summary.error)} />
      ) : (
        <>
          <Panel
            title="真实工具链门禁"
            actions={
              <Badge value={String(data?.gates?.overall ?? "not_run")}>
                {String(data?.gates?.overall ?? "not_run")}
              </Badge>
            }
          >
            {gateRows.length === 0 ? (
              <EmptyState
                title="暂无门禁结果"
                description="运行 run_real_provider_gates.py 后显示在这里。"
                action={
                  <button
                    className="btn btn-sm"
                    onClick={() => void summary.refetch()}
                  >
                    刷新
                  </button>
                }
              />
            ) : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>场景</th>
                      <th>结果</th>
                      <th>Verified</th>
                      <th>证据门禁</th>
                    </tr>
                  </thead>
                  <tbody>
                    {gateRows.map((row) => (
                      <tr key={String(row.scenario)}>
                        <td>{String(row.scenario)}</td>
                        <td>
                          <Badge value={String(row.assertion)}>
                            {String(row.assertion)}
                          </Badge>
                        </td>
                        <td>{String(row.verified ?? "-")}</td>
                        <td>{String(row.evidence_gate ?? "-")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>

          <Panel
            title="Lab 门禁"
            actions={
              <Badge value={String(data?.lab_gates?.overall ?? "not_run")}>
                {String(data?.lab_gates?.overall ?? "not_run")}
              </Badge>
            }
          >
            {labGateRows.length === 0 ? (
              <EmptyState
                title="暂无 Lab 门禁结果"
                description="运行 run_lab_gates.py 后显示在这里。"
                action={
                  <button
                    className="btn btn-sm"
                    onClick={() => void summary.refetch()}
                  >
                    刷新
                  </button>
                }
              />
            ) : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>场景</th>
                      <th>结果</th>
                    </tr>
                  </thead>
                  <tbody>
                    {labGateRows.map((row) => (
                      <tr key={String(row.scenario)}>
                        <td>{String(row.scenario)}</td>
                        <td>
                          <Badge value={String(row.assertion)}>
                            {String(row.assertion)}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>

          <Panel
            title="六工具真实矩阵"
            actions={
              <Badge value={String(data?.tool_matrix?.assertion ?? "not_run")}>
                {String(data?.tool_matrix?.assertion ?? "not_run")}
              </Badge>
            }
          >
            {toolMatrixTools.length === 0 ? (
              <EmptyState
                title="未运行六工具矩阵"
                description="运行 real-tool-matrix 套件后展示。"
                action={
                  <button
                    className="btn btn-sm"
                    onClick={() => void summary.refetch()}
                  >
                    刷新
                  </button>
                }
              />
            ) : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>工具</th>
                      <th>目标</th>
                      <th>Verified</th>
                      <th>Gate</th>
                    </tr>
                  </thead>
                  <tbody>
                    {toolMatrixTools.map((tool, index) => (
                      <tr key={`${String(tool.tool)}-${index}`}>
                        <td className="mono">{String(tool.tool)}</td>
                        <td>{String(tool.target)}</td>
                        <td>{String(tool.verified ?? "-")}</td>
                        <td>
                          <Badge value={tool.gate_pass ? "pass" : "fail"}>
                            {tool.gate_pass ? "pass" : "fail"}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>

          <Panel
            title="MCP 真实连接"
            actions={
              <Badge value={String(data?.mcp_real?.assertion ?? "not_run")}>
                {String(data?.mcp_real?.assertion ?? "not_run")}
              </Badge>
            }
          >
            {Object.keys(mcpServers).length === 0 ? (
              <EmptyState
                title="未运行 MCP smoke"
                description="运行 mcp-real 套件后展示。"
              />
            ) : (
              <div className="stack" style={{ gap: 8 }}>
                {Object.entries(mcpServers).map(([name, info]) => (
                  <div className="form-row" key={name}>
                    <code>{name}</code>
                    <Badge value={String(info?.status ?? "unknown")}>
                      {String(info?.status ?? "unknown")}
                    </Badge>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel
            title="完整默认验收"
            actions={
              <Badge value={String(data?.acceptance_all?.overall ?? "not_run")}>
                {String(data?.acceptance_all?.overall ?? "not_run")}
              </Badge>
            }
          >
            {acceptanceSteps.length === 0 ? (
              <EmptyState
                title="未运行完整验收"
                description="运行 acceptance_gate.py 后展示。"
              />
            ) : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>套件</th>
                      <th>状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {acceptanceSteps.map((step) => (
                      <tr key={String(step.name)}>
                        <td>{String(step.name)}</td>
                        <td>
                          <Badge value={String(step.status)}>
                            {String(step.status)}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>

          <Panel title="RAG 检索基准">
            {ragRows.length === 0 ? (
              <EmptyState
                title="未运行 RAG 基准"
                description="需要真实 Qdrant + Ollama 环境。"
                action={
                  <button
                    className="btn btn-sm"
                    onClick={() => void summary.refetch()}
                  >
                    刷新
                  </button>
                }
              />
            ) : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>档位</th>
                      <th>hit rate</th>
                      <th>p95 (ms)</th>
                      <th>degraded</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ragRows.map((row, index) => (
                      <tr key={index}>
                        <td>{String(row.rag_level)}</td>
                        <td>{String(row.hit_rate)}</td>
                        <td>{String(row.p95_ms)}</td>
                        <td>{String(row.degraded)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>

          <Panel
            title="Profile 工程"
            actions={
              <Badge value={String(deterministicProfile.overall ?? "not_run")}>
                {String(deterministicProfile.overall ?? "not_run")}
              </Badge>
            }
          >
            <div className="memory-summary">
              <span>
                确定性覆盖 {String(deterministicProfile.overall ?? "-")}
              </span>
              <span>
                Preset 真实 {String(realPreset.overall ?? "not_run")} ·{" "}
                {String(realPreset.preset_id ?? "-")}
              </span>
              <span>Presets {profileEngineering?.preset_count ?? 0}</span>
              <span>
                AD/Cloud fixture{" "}
                {String(externalFixture.overall ?? "pending")} · real{" "}
                {String(externalFixture.real_environment ?? "pending")}
              </span>
              <span>
                Preset fixtures{" "}
                {String(presetFixtures.overall ?? "not_run")} ·{" "}
                {String(presetFixtures.preset_count ?? 0)} presets
              </span>
            </div>
            {baselinePreset && presetGroup ? (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>分组</th>
                      <th>Actions</th>
                      <th>Duplicates</th>
                      <th>Verified</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>Baseline</td>
                      <td>
                        {String(baselineMean.tool_calls ?? "-")} ±{" "}
                        {String(baselineStd.tool_calls ?? "-")}
                      </td>
                      <td>{String(baselineMean.duplicate_actions ?? "-")}</td>
                      <td>{String(baselineMean.verified_findings ?? "-")}</td>
                    </tr>
                    <tr>
                      <td>{String(realPreset.preset_id ?? "Preset")}</td>
                      <td>
                        {String(presetMean.tool_calls ?? "-")} ±{" "}
                        {String(presetStd.tool_calls ?? "-")}
                      </td>
                      <td>{String(presetMean.duplicate_actions ?? "-")}</td>
                      <td>{String(presetMean.verified_findings ?? "-")}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState
                title="暂无 Profile 工程基准"
                description="运行 profile-context 与多轮 Preset 真实基准后显示在这里。"
                action={
                  <button
                    className="btn btn-sm"
                    onClick={() => void summary.refetch()}
                  >
                    刷新
                  </button>
                }
              />
            )}
          </Panel>

          <Panel title="工具镜像执行矩阵">
            {smokeRows.length === 0 ? (
              <EmptyState
                title="未运行工具镜像 smoke"
                description="运行工具镜像 smoke 后显示在这里。"
                action={
                  <button
                    className="btn btn-sm"
                    onClick={() => void summary.refetch()}
                  >
                    刷新
                  </button>
                }
              />
            ) : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>工具</th>
                      <th>定义</th>
                      <th>目标</th>
                      <th>状态</th>
                      <th>结果</th>
                    </tr>
                  </thead>
                  <tbody>
                    {smokeRows.map((row, index) => (
                      <tr key={index}>
                        <td>{String(row.tool)}</td>
                        <td className="mono">{String(row.definition)}</td>
                        <td>{String(row.target)}</td>
                        <td>
                          <Badge value={String(row.status)}>
                            {String(row.status)}
                          </Badge>
                        </td>
                        <td className="muted">{String(row.detail)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>

          <Panel
            title="Release Readiness"
            actions={
              <Badge value={String(data?.readiness?.overall ?? "not_run")}>
                {String(data?.readiness?.overall ?? "not_run")}
              </Badge>
            }
          >
            <div className="memory-summary">
              <span>Python {String(pythonPassed ?? "?")} 项通过</span>
              <span>npm {String(tsStatus ?? "?")}</span>
            </div>
            {data?.readiness?.overall === "not_run" ? (
              <Notice tone="warn">
                本环境未生成 readiness JSON，运行 readiness_cli --out 后刷新。
              </Notice>
            ) : null}
          </Panel>
        </>
      )}
    </section>
  );
}
