import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Boxes,
  Braces,
  Database,
  Download,
  Globe,
  PlugZap,
  RefreshCw,
  Save,
  Server,
  Trash2,
  Upload,
} from "lucide-react";
import { control } from "../api.js";
import { ErrorBanner, Loading } from "../components/Status.js";
import { Badge, EmptyState, Kpi, Notice, Panel } from "../components/ui.js";

type TabId = "assets" | "components";

export function AssetsPage() {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<TabId>("assets");
  const [assetProject, setAssetProject] = useState("");
  const [assetKind, setAssetKind] = useState("url");
  const [assetValue, setAssetValue] = useState("");
  const [assetStatus, setAssetStatus] = useState("known");
  const [importProject, setImportProject] = useState("");
  const [projectFilter, setProjectFilter] = useState("");
  const [dispatchNode, setDispatchNode] = useState("");
  const [dispatchTaskRef, setDispatchTaskRef] = useState("");
  const [dispatchCommand, setDispatchCommand] = useState("");
  const [dispatchResult, setDispatchResult] = useState<Record<
    string,
    unknown
  > | null>(null);
  const [dispatchError, setDispatchError] = useState<string | null>(null);
  const [importResult, setImportResult] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => control.listProjects(),
  });
  const assets = useQuery({
    queryKey: ["assets", projectFilter],
    queryFn: () => control.listAssets(projectFilter || undefined),
    refetchInterval: 5000,
  });
  const lifecycle = useQuery({
    queryKey: ["asset-lifecycle"],
    queryFn: () => control.listAssetLifecycle(),
  });
  const tools = useQuery({
    queryKey: ["runtime-tools"],
    queryFn: () => control.requestPublic("/api/v1/runtime/tools"),
  });
  const skills = useQuery({
    queryKey: ["runtime-skills"],
    queryFn: () => control.requestPublic("/api/v1/runtime/skills"),
  });
  const mcp = useQuery({
    queryKey: ["runtime-mcp"],
    queryFn: () => control.requestPublic("/api/v1/runtime/mcp"),
  });
  const knowledge = useQuery({
    queryKey: ["knowledge-list"],
    queryFn: () => control.requestPublic("/api/v1/knowledge"),
  });
  const diagnostics = useQuery({
    queryKey: ["diagnostics"],
    queryFn: () => control.requestPublic("/api/v1/diagnostics"),
    refetchInterval: 5000,
  });
  const remoteNodes = useQuery({
    queryKey: ["remote-nodes"],
    queryFn: () => control.requestPublic("/api/v1/remote/nodes"),
    refetchInterval: 8000,
  });
  const dispatchNodeTask = useMutation({
    mutationFn: async () => {
      const result = await control.requestJson<Record<string, unknown>>(
        "POST",
        `/api/v1/remote/nodes/${encodeURIComponent(dispatchNode)}/dispatch`,
        {
          task_ref:
            dispatchTaskRef.trim() || `web_${Date.now()}`,
          payload: { command: dispatchCommand.trim().split(/\s+/) },
          lease_seconds: 600,
        },
      );
      return result;
    },
    onSuccess: (result) => {
      setDispatchResult(result);
      setDispatchError(null);
      void queryClient.invalidateQueries({ queryKey: ["remote-nodes"] });
    },
    onError: (error) => {
      setDispatchError(String(error));
      setDispatchResult(null);
    },
  });
  const createAsset = useMutation({
    mutationFn: () =>
      control.createAsset({
        project_id: assetProject,
        kind: assetKind,
        value: assetValue,
        status: assetStatus,
        source: "manual",
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["assets"] });
      setAssetValue("");
      setSuccessMessage("资产已新增");
    },
  });
  const updateAsset = useMutation({
    mutationFn: (args: { id: string; status: string }) =>
      control.updateAsset(args.id, { status: args.status }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["assets"] });
      setSuccessMessage("资产状态已更新");
    },
  });
  const deleteAsset = useMutation({
    mutationFn: (id: string) => control.deleteAsset(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["assets"] });
      setSuccessMessage("资产已删除");
    },
  });
  const importTargets = useMutation({
    mutationFn: () => control.importTargetAssets(importProject),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["assets"] });
      setSuccessMessage("目标已导入为资产");
    },
  });
  const exportAssets = () => {
    const blob = new Blob([JSON.stringify(assetRows, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "assets.json";
    anchor.click();
    URL.revokeObjectURL(url);
    setSuccessMessage("资产 JSON 已导出");
  };
  const importAssetsFile = async (file: File) => {
    try {
      const text = await file.text();
      const parsed = JSON.parse(text) as unknown;
      const rows = Array.isArray(parsed)
        ? parsed
        : Array.isArray((parsed as { assets?: unknown }).assets)
          ? ((parsed as { assets: Array<Record<string, unknown>> }).assets)
          : [parsed as Record<string, unknown>];
      setImporting(true);
      let imported = 0;
      for (const row of rows) {
        const projectId =
          String(row.project_id ?? assetProject ?? "");
        if (!projectId || !row.value) {
          continue;
        }
        await control.createAsset({
          project_id: projectId,
          kind: String(row.kind ?? "url"),
          value: String(row.value),
          source: String(row.source ?? "manual"),
          status: String(row.status ?? "known"),
          metadata: (row.metadata as Record<string, unknown>) ?? {},
        });
        imported += 1;
      }
      setImportResult(`导入 ${imported} 条资产`);
      void queryClient.invalidateQueries({ queryKey: ["assets"] });
    } catch (error) {
      setImportResult(`导入失败: ${String(error)}`);
    } finally {
      setImporting(false);
    }
  };
  const assetRows = (assets.data ?? []) as Array<Record<string, unknown>>;
  const lifecycleRows = (
    (lifecycle.data as { lifecycle?: string[] } | undefined)?.lifecycle ?? []
  ) as string[];
  const toolRows = (tools.data as Array<Record<string, unknown>> | undefined) ?? [];
  const skillRows = (skills.data as Array<Record<string, unknown>> | undefined) ?? [];
  const mcpRows = (mcp.data as Array<Record<string, unknown>> | undefined) ?? [];
  const knowledgeRows = (knowledge.data as Array<Record<string, unknown>> | undefined) ?? [];
  const storage = (
    diagnostics.data as
      | { storage?: Record<string, unknown> | undefined }
      | undefined
  )?.storage;
  const storageEntries = Object.entries(storage ?? {}).filter(
    ([key]) => key !== "available",
  );
  const findingAssets = assetRows.filter(
    (asset) => Number(asset.finding_count ?? 0) > 0,
  ).length;

  return (
    <section>
      <header className="page-head">
        <div className="page-head-copy">
          <p className="page-eyebrow">Assets & Components</p>
          <h1>资产与组件</h1>
          <p className="page-sub">
            管理测试范围内的资产对象，并查看工具、技能、MCP 与知识注册状态。
          </p>
        </div>
        <div className="actions">
          <button
            className="btn"
            onClick={() => {
              void assets.refetch();
              void diagnostics.refetch();
              void tools.refetch();
              void skills.refetch();
              void mcp.refetch();
              void knowledge.refetch();
            }}
          >
            <RefreshCw className="" />
            刷新
          </button>
        </div>
      </header>
      {successMessage ? (
        <Notice tone="ok">{successMessage}</Notice>
      ) : null}
      <div className="tabs">
        <button
          className={`tab${tab === "assets" ? " active" : ""}`}
          onClick={() => setTab("assets")}
        >
          <Globe className="" />
          资产对象
        </button>
        <button
          className={`tab${tab === "components" ? " active" : ""}`}
          onClick={() => setTab("components")}
        >
          <Boxes className="" />
          组件注册
        </button>
      </div>
      <div className="kpi-grid">
        <Kpi label="资产对象" value={assetRows.length} tone="info" note="域名 / URL / 服务" />
        <Kpi
          label="有发现资产"
          value={findingAssets}
          tone={findingAssets ? "warn" : undefined}
          note="关联 finding"
        />
        <Kpi label="工具" value={toolRows.length} tone="ok" note="ToolRegistry" />
        <Kpi label="技能" value={skillRows.length} note="Skills" />
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
      </div>

      {tab === "assets" ? (
        <Panel
          title="资产对象"
          icon={Globe}
          actions={
            <span className="muted" style={{ fontSize: 12 }}>
              {assetRows.length} 项
            </span>
          }
        >
          <div
            id="asset-form"
            className="panel"
            style={{ marginBottom: 12 }}
          >
            <div className="form-grid">
              <label className="field">
                项目 id
                <input
                  value={assetProject}
                  onChange={(event) => setAssetProject(event.target.value)}
                  placeholder="project_..."
                />
              </label>
              <label className="field">
                类型
                <select
                  value={assetKind}
                  onChange={(event) => setAssetKind(event.target.value)}
                >
                  <option value="url">url</option>
                  <option value="domain">domain</option>
                  <option value="ip">ip</option>
                  <option value="host">host</option>
                  <option value="service">service</option>
                </select>
              </label>
              <label className="field">
                值
                <input
                  value={assetValue}
                  onChange={(event) => setAssetValue(event.target.value)}
                  placeholder="https://lab.example.test"
                />
              </label>
              <label className="field">
                状态
                <select
                  value={assetStatus}
                  onChange={(event) => setAssetStatus(event.target.value)}
                >
                  <option value="known">known</option>
                  <option value="active">active</option>
                  <option value="retired">retired</option>
                </select>
              </label>
              <div className="actions" style={{ alignItems: "flex-end" }}>
                <button
                  className="btn btn-primary"
                  onClick={() => createAsset.mutate()}
                  disabled={createAsset.isPending || !assetProject.trim() || !assetValue.trim()}
                  title={
                    !assetProject.trim()
                      ? "请先填写项目 ID"
                      : !assetValue.trim()
                        ? "请先填写资产值"
                        : createAsset.isPending
                          ? "正在创建..."
                          : undefined
                  }
                >
                  <Save className="" />
                  新增资产
                </button>
              </div>
            </div>
          </div>
          <div className="panel" style={{ marginBottom: 12 }}>
            <div className="form-grid">
              <label className="field">
                从项目导入目标为资产
                <input
                  value={importProject}
                  onChange={(event) => setImportProject(event.target.value)}
                  placeholder="project_..."
                />
              </label>
              <div className="actions" style={{ alignItems: "flex-end" }}>
                <button
                  className="btn"
                  onClick={() => importTargets.mutate()}
                  disabled={importTargets.isPending || !importProject.trim()}
                  title={
                    !importProject.trim()
                      ? "请先填写项目 ID"
                      : importTargets.isPending
                        ? "正在导入..."
                        : undefined
                  }
                >
                  导入目标
                </button>
              </div>
            </div>
          </div>
          <div className="panel" style={{ marginBottom: 12 }}>
            <div className="actions" style={{ marginBottom: 0 }}>
              <button
                className="btn"
                onClick={exportAssets}
                disabled={assetRows.length === 0}
                title={assetRows.length === 0 ? "当前没有可导出的资产" : undefined}
              >
                <Download className="" />
                导出 JSON
              </button>
              <button
                className="btn"
                onClick={() => fileInputRef.current?.click()}
                disabled={importing}
                title={importing ? "正在导入 JSON..." : undefined}
              >
                <Upload className="" />
                {importing ? "导入中..." : "导入 JSON"}
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept="application/json,.json"
                style={{ display: "none" }}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) {
                    void importAssetsFile(file);
                  }
                  event.target.value = "";
                }}
              />
            </div>
            {importResult ? (
              <Notice
                tone={
                  importResult.startsWith("导入失败") ? "error" : "ok"
                }
              >
                {importResult}
              </Notice>
            ) : null}
          </div>
          {assets.isLoading ? (
            <Loading label="加载资产" />
          ) : assets.isError ? (
            <ErrorBanner message={String(assets.error)} />
          ) : assetRows.length === 0 ? (
            <EmptyState
              title="暂无资产"
              description="创建目标或手动新增资产后显示。"
              action={
                <button
                  className="btn btn-sm"
                  onClick={() => {
                    setTab("assets");
                    document
                      .getElementById("asset-form")
                      ?.scrollIntoView({ behavior: "smooth" });
                  }}
                >
                  新增资产
                </button>
              }
            />
          ) : (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Asset</th>
                    <th>类型</th>
                    <th>值</th>
                    <th>来源</th>
                    <th>状态</th>
                    <th>发现</th>
                    <th>最后发现</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {assetRows.map((asset) => (
                    <tr key={String(asset.asset_id)}>
                      <td className="mono">{String(asset.asset_id).slice(0, 16)}</td>
                      <td>{String(asset.kind)}</td>
                      <td className="mono">{String(asset.value)}</td>
                      <td>{String(asset.source)}</td>
                      <td>
                        <Badge value={String(asset.status)}>{String(asset.status)}</Badge>
                      </td>
                      <td className="num">{String(asset.finding_count ?? 0)}</td>
                      <td className="muted">{String(asset.last_seen ?? "")}</td>
                      <td>
                        <div className="btn-group">
                          <select
                            className="btn btn-sm"
                            value={String(asset.status)}
                            onChange={(event) =>
                              updateAsset.mutate({
                                id: String(asset.asset_id),
                                status: event.target.value,
                              })
                            }
                            aria-label="资产生命周期"
                          >
                            {lifecycleRows.map((status) => (
                              <option key={status} value={status}>
                                {status}
                              </option>
                            ))}
                          </select>
                          <button
                            className="btn btn-sm btn-danger"
                            onClick={() => {
                              if (
                                window.confirm(
                                  `确定删除资产 ${String(asset.value).slice(0, 40)}？`,
                                )
                              ) {
                                deleteAsset.mutate(String(asset.asset_id));
                              }
                            }}
                          >
                            <Trash2 className="" />
                            删除
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {createAsset.isError ? (
            <Notice tone="error">{String(createAsset.error)}</Notice>
          ) : null}
          {importTargets.isError ? (
            <Notice tone="error">{String(importTargets.error)}</Notice>
          ) : null}
        </Panel>
      ) : null}

      {tab === "components" ? (
        <>
          <Panel
            title="存储后端"
            icon={Database}
            actions={
              <span className="muted" style={{ fontSize: 12 }}>
                {storageEntries.length} 类
              </span>
            }
          >
            <div className="card-grid">
              {storageEntries.map(([key, value]) => (
                <div className="card" key={key}>
                  <div className="panel-head" style={{ marginBottom: 4 }}>
                    <div className="card-title" style={{ margin: 0 }}>
                      <code>{key}</code>
                    </div>
                    <Badge value={String((value as Record<string, string>).status ?? "ok")}>
                      {String((value as Record<string, string>).status ?? "ok")}
                    </Badge>
                  </div>
                  <pre className="result-box" style={{ maxHeight: 150, fontSize: 11 }}>
                    {JSON.stringify(value, null, 2)}
                  </pre>
                </div>
              ))}
            </div>
          </Panel>
          <Panel
            title="工具注册表"
            icon={Server}
            actions={<span className="muted" style={{ fontSize: 12 }}>{toolRows.length} 项</span>}
          >
            {toolRows.length === 0 ? (
              <EmptyState title="暂无工具" description="工具注册表为空。" />
            ) : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>tool_ref</th>
                      <th>capability</th>
                      <th>status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {toolRows.map((row, index) => (
                      <tr key={index}>
                        <td className="mono">{String(row.tool_ref)}</td>
                        <td>{String(row.capability)}</td>
                        <td>
                          <Badge value={String(row.status)}>{String(row.status)}</Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
          <Panel
            title="技能"
            icon={Boxes}
            actions={<span className="muted" style={{ fontSize: 12 }}>{skillRows.length} 项</span>}
          >
            {skillRows.length === 0 ? (
              <EmptyState title="暂无技能" description="技能注册表为空。" />
            ) : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>skill_ref</th>
                      <th>version</th>
                      <th>runner</th>
                      <th>risk_level</th>
                      <th>status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {skillRows.map((row, index) => (
                      <tr key={index}>
                        <td className="mono">{String(row.skill_ref)}</td>
                        <td>{String(row.version)}</td>
                        <td>{String(row.runner)}</td>
                        <td>{String(row.risk_level)}</td>
                        <td>
                          <Badge value={String(row.status)}>{String(row.status)}</Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
          <Panel
            title="MCP 服务器"
            icon={PlugZap}
            actions={<span className="muted" style={{ fontSize: 12 }}>{mcpRows.length} 项</span>}
          >
            {mcpRows.length === 0 ? (
              <EmptyState title="暂无 MCP" description="MCP 注册表为空。" />
            ) : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>server_id</th>
                      <th>name</th>
                      <th>kind</th>
                      <th>status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {mcpRows.map((row, index) => (
                      <tr key={index}>
                        <td className="mono">{String(row.server_id)}</td>
                        <td>{String(row.name)}</td>
                        <td>{String(row.kind)}</td>
                        <td>
                          <Badge value={String(row.status)}>{String(row.status)}</Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
          <Panel
            title="远程节点"
            icon={Server}
            actions={
              <span className="muted" style={{ fontSize: 12 }}>
                {(remoteNodes.data as Array<Record<string, unknown>> | undefined)?.length ?? 0} 个节点
              </span>
            }
          >
            {((remoteNodes.data as Array<Record<string, unknown>> | undefined) ?? []).length === 0 ? (
              <EmptyState
                title="暂无远程节点"
                description="agent-node 注册后显示在线状态、能力与心跳。"
              />
            ) : (
              <>
                <div className="form-grid" style={{ marginBottom: 12 }}>
                  <label className="field">
                    执行节点
                    <select
                      value={dispatchNode}
                      onChange={(event) => setDispatchNode(event.target.value)}
                    >
                      <option value="">选择在线节点</option>
                      {(
                        (remoteNodes.data as
                          | Array<Record<string, unknown>>
                          | undefined) ?? []
                      )
                        .filter((node) => node.status === "online")
                        .map((node) => (
                          <option
                            key={String(node.node_id)}
                            value={String(node.node_id)}
                          >
                            {String(node.node_id)}（{String(node.status)}）
                          </option>
                        ))}
                    </select>
                  </label>
                  <label className="field">
                    Task ref（可选）
                    <input
                      value={dispatchTaskRef}
                      onChange={(event) => setDispatchTaskRef(event.target.value)}
                      placeholder="留空自动生成"
                    />
                  </label>
                  <label className="field" style={{ gridColumn: "1 / -1" }}>
                    命令
                    <input
                      value={dispatchCommand}
                      onChange={(event) => setDispatchCommand(event.target.value)}
                      placeholder="nmap -sV http://target/ 或 curl -sS target"
                    />
                  </label>
                </div>
                <div className="btn-group" style={{ marginBottom: 12 }}>
                  <button
                    className="btn btn-primary"
                    onClick={() => dispatchNodeTask.mutate()}
                    disabled={
                      !dispatchNode ||
                      !dispatchCommand.trim() ||
                      dispatchNodeTask.isPending
                    }
                  >
                    {dispatchNodeTask.isPending ? "派发中..." : "派发任务"}
                  </button>
                </div>
                {dispatchError ? (
                  <Notice tone="error">{dispatchError}</Notice>
                ) : null}
                {dispatchResult ? (
                  <Notice tone="ok">
                    已派发{" "}
                    {String(
                      (
                        dispatchResult.dispatch as
                          | Record<string, unknown>
                          | undefined
                      )?.task_ref ?? "",
                    )}
                    ，lease{" "}
                    {String(
                      (
                        dispatchResult.lease as
                          | Record<string, unknown>
                          | undefined
                      )?.lease_id ?? "",
                    )}
                  </Notice>
                ) : null}
                <div className="table-wrap">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>node_id</th>
                        <th>version</th>
                        <th>能力</th>
                        <th>状态</th>
                        <th>最近心跳</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(
                        (remoteNodes.data as
                          | Array<Record<string, unknown>>
                          | undefined) ?? []
                      ).map((node) => (
                        <tr key={String(node.node_id)}>
                          <td className="mono">{String(node.node_id)}</td>
                          <td>{String(node.version)}</td>
                          <td className="muted">
                            {(node.capabilities as unknown[] | undefined)?.join(
                              ", ",
                            ) ?? "-"}
                          </td>
                          <td>
                            <Badge value={String(node.status)}>
                              {String(node.status)}
                            </Badge>
                          </td>
                          <td className="muted">
                            {String(node.last_seen_at ?? "-")}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </Panel>
          <Panel
            title="知识库"
            icon={Braces}
            actions={<span className="muted" style={{ fontSize: 12 }}>{knowledgeRows.length} 条</span>}
          >
            {knowledgeRows.length === 0 ? (
              <EmptyState title="暂无知识分块" description="知识库为空。" />
            ) : (
              <div className="card-grid">
                {knowledgeRows.slice(0, 12).map((chunk) => (
                  <div className="card" key={String(chunk.chunk_id)}>
                    <div className="card-title">
                      <code>{String(chunk.chunk_id).slice(0, 20)}</code>
                    </div>
                    <p className="card-meta">
                      来源 {String(chunk.source_ref ?? "-")} · v
                      {String(chunk.version ?? "1")}
                    </p>
                    <p className="muted" style={{ fontSize: 12 }}>
                      {String(chunk.content ?? "").slice(0, 140)}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </>
      ) : null}
    </section>
  );
}
