import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpen,
  FilePlus2,
  Network,
  Pencil,
  RefreshCw,
  Search,
  Trash2,
  Upload,
} from "lucide-react";
import { control, CONTROL_URL } from "../api.js";
import { ErrorBanner, Loading } from "../components/Status.js";
import { MarkdownView } from "../components/MarkdownView.js";
import {
  Badge,
  EmptyState,
  Kpi,
  Notice,
  Panel,
  SyncStamp,
} from "../components/ui.js";
import { KnowledgeGraphView } from "../components/KnowledgeGraphView.js";

type KnowledgeRow = {
  chunk_id: string;
  source_ref: string;
  project_id?: string;
  trust: string;
  subjects?: string[];
  target_refs?: string[];
  observed_at?: string;
  expires_at?: string | null;
  content?: string;
  updated_at?: string;
};

type TabId = "browse" | "add" | "import" | "search" | "graph";

const TABS: Array<{ id: TabId; label: string; icon: typeof BookOpen }> = [
  { id: "browse", label: "浏览", icon: BookOpen },
  { id: "add", label: "新增", icon: FilePlus2 },
  { id: "import", label: "导入", icon: Upload },
  { id: "search", label: "检索", icon: Search },
  { id: "graph", label: "图谱", icon: Network },
];

type GraphNode = {
  id: string;
  label: string;
  type: string;
  chunks: number;
};

type GraphEdge = {
  source: string;
  target: string;
  predicate: string;
};

type KnowledgeGraph = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  counts: { nodes: number; edges: number; chunks: number };
};

export function KnowledgePage() {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<TabId>("browse");
  const [content, setContent] = useState("");
  const [sourceRef, setSourceRef] = useState("web");
  const [project, setProject] = useState("");
  const [chunkId, setChunkId] = useState("");
  const [subjects, setSubjects] = useState("web");
  const [targetRefs, setTargetRefs] = useState("");
  const [observedAt, setObservedAt] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  const [importSource, setImportSource] = useState("web/imported");
  const [importLicense, setImportLicense] = useState("unknown");
  const [importVersion, setImportVersion] = useState("1");
  const [importMarkdown, setImportMarkdown] = useState("");
  const [importFile, setImportFile] = useState<File | null>(null);
  const [searchResults, setSearchResults] = useState<KnowledgeRow[] | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [searchTarget, setSearchTarget] = useState("");
  const [searchSince, setSearchSince] = useState("");
  const [searchUntil, setSearchUntil] = useState("");
  const [searchLoading, setSearchLoading] = useState(false);
  const [successMessage, setSuccessMessage] = useState("");
  const knowledge = useQuery({
    queryKey: ["knowledge", project],
    queryFn: () =>
      control.requestPublic(
        `/api/v1/knowledge${
          project ? `?project_id=${encodeURIComponent(project)}` : ""
        }`,
      ) as Promise<unknown>,
    refetchInterval: 5000,
  });
  const audit = useQuery({
    queryKey: ["knowledge-audit"],
    queryFn: () =>
      control.requestPublic(
        "/api/v1/knowledge/events",
      ) as unknown as Promise<{
        total: number;
        events: Array<{
          event_id: string;
          event_type: string;
          occurred_at: string;
          payload: { chunk_id?: string; revision?: number };
        }>;
      }>,
    refetchInterval: 5000,
  });
  const graph = useQuery({
    queryKey: ["knowledge-graph"],
    queryFn: () =>
      control.requestPublic(
        "/api/v1/knowledge/graph",
      ) as unknown as Promise<KnowledgeGraph>,
    refetchInterval: 15000,
  });
  const add = useMutation({
    mutationFn: async () => {
      const response = await fetch(
        editingId
          ? `${CONTROL_URL}/api/v1/knowledge/${editingId}`
          : `${CONTROL_URL}/api/v1/knowledge`,
        {
          method: editingId ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            chunk_id: editingId || chunkId || `web_${Date.now()}`,
            source_ref: sourceRef,
            project_id: project,
            content,
            subjects: subjects
              .split(",")
              .map((item) => item.trim())
              .filter(Boolean),
            target_refs: targetRefs
              .split(",")
              .map((item) => item.trim())
              .filter(Boolean),
            observed_at: observedAt,
            expires_at: expiresAt || null,
          }),
        },
      );
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      return response.json();
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["knowledge"] });
      void queryClient.invalidateQueries({ queryKey: ["knowledge-audit"] });
      setContent("");
      setEditingId(null);
      setSuccessMessage(editingId ? "修改已保存" : "知识分块已新增");
      setActiveTab("browse");
    },
  });
  const importMarkdownMutation = useMutation({
    mutationFn: async () => {
      const response = await fetch(
        `${CONTROL_URL}/api/v1/knowledge/import`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            source_id: importSource,
            license: importLicense,
            version: importVersion,
            project_id: project,
            content: importMarkdown,
            subjects: subjects
              .split(",")
              .map((item) => item.trim())
              .filter(Boolean),
            target_refs: targetRefs
              .split(",")
              .map((item) => item.trim())
              .filter(Boolean),
          }),
        },
      );
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      return response.json();
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["knowledge"] });
      void queryClient.invalidateQueries({ queryKey: ["knowledge-audit"] });
      setImportMarkdown("");
      setSuccessMessage("Markdown 已导入");
      setActiveTab("browse");
    },
  });
  const importFileMutation = useMutation({
    mutationFn: async () => {
      if (!importFile) {
        throw new Error("请先选择要导入的文件");
      }
      const form = new FormData();
      form.append("file", importFile);
      form.append("source_id", importSource);
      form.append("license", importLicense);
      form.append("version", importVersion);
      form.append("project_id", project);
      const response = await fetch(
        `${CONTROL_URL}/api/v1/knowledge/import-file`,
        {
          method: "POST",
          body: form,
        },
      );
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `HTTP ${response.status}`);
      }
      return response.json();
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["knowledge"] });
      void queryClient.invalidateQueries({ queryKey: ["knowledge-audit"] });
      setImportFile(null);
      setSuccessMessage("文档已导入");
      setActiveTab("browse");
    },
  });
  const remove = useMutation({
    mutationFn: async (chunkId: string) => {
      const response = await fetch(
        `${CONTROL_URL}/api/v1/knowledge/${chunkId}`,
        { method: "DELETE" },
      );
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      return response.json();
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["knowledge"] });
      void queryClient.invalidateQueries({ queryKey: ["knowledge-audit"] });
      setSuccessMessage("知识分块已删除");
      setSearchResults(null);
    },
  });
  const rows = (searchResults ??
    (knowledge.data as KnowledgeRow[] | undefined) ??
    []) as KnowledgeRow[];

  return (
    <section>
      <header className="page-head">
        <div className="page-head-copy">
          <p className="page-eyebrow">Knowledge Base</p>
          <h1>知识库</h1>
          <p className="page-sub">
            管理知识分块、来源信任与审计记录，供 Agent 上下文检索使用。
          </p>
          <SyncStamp dataUpdatedAt={knowledge.dataUpdatedAt} />
        </div>
        <div className="actions">
          <button className="btn" onClick={() => void knowledge.refetch()}>
            <RefreshCw className="" />
            刷新
          </button>
        </div>
      </header>
      {successMessage ? (
        <Notice tone="ok">{successMessage}</Notice>
      ) : null}
      <div className="kpi-grid">
        <Kpi
          label="知识分块"
          value={(knowledge.data as KnowledgeRow[] | undefined)?.length ?? 0}
          tone="info"
          note="当前筛选范围"
        />
        <Kpi
          label="审计事件"
          value={audit.data?.total ?? 0}
          tone="ok"
          note="变更可追溯"
        />
        <Kpi
          label="检索结果"
          value={searchResults?.length ?? 0}
          note="最近一次检索"
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

      {activeTab === "browse" ? (
        <>
          <div className="toolbar">
            <input
              type="text"
              aria-label="项目过滤"
              placeholder="项目 id（留空为全局）"
              value={project}
              onChange={(event) => setProject(event.target.value)}
            />
            <button
              className="btn"
              onClick={() => void knowledge.refetch()}
            >
              应用筛选
            </button>
          </div>
          <Panel
            title="知识分块"
            icon={BookOpen}
            actions={<span className="muted" style={{ fontSize: 12 }}>{rows.length} 条</span>}
          >
            {knowledge.isLoading ? (
              <Loading label="加载知识库" />
            ) : knowledge.isError ? (
              <ErrorBanner message={String(knowledge.error)} />
            ) : rows.length === 0 ? (
              <EmptyState
                title="暂无知识分块"
                description="新增或导入内容后显示。"
                action={
                  <button
                    className="btn btn-sm"
                    onClick={() => setActiveTab("add")}
                  >
                    新增知识
                  </button>
                }
              />
            ) : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Chunk</th>
                      <th>来源</th>
                      <th>项目</th>
                      <th>信任</th>
                      <th>主题</th>
                      <th>目标</th>
                      <th>内容</th>
                      <th>更新</th>
                      <th className="sticky-right">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.chunk_id}>
                        <td className="mono">{row.chunk_id.slice(0, 20)}</td>
                        <td>{row.source_ref}</td>
                        <td>{row.project_id ?? "全局"}</td>
                        <td className="sticky-right">
                          <Badge value={row.trust}>{row.trust}</Badge>
                        </td>
                        <td>{(row.subjects ?? []).join(", ")}</td>
                        <td className="muted">
                          {(row.target_refs ?? []).join(", ") || "全局"}
                        </td>
                        <td style={{ maxWidth: 320 }}>
                          {row.content ? (
                            <details>
                              <summary>
                                {row.content.slice(0, 60)}
                                {row.content.length > 60 ? "..." : ""}
                              </summary>
                              <div
                                style={{
                                  margin: "6px 0 0",
                                  maxHeight: 220,
                                  overflow: "auto",
                                }}
                              >
                                <MarkdownView value={row.content} />
                              </div>
                            </details>
                          ) : (
                            <span className="muted">无内容</span>
                          )}
                        </td>
                        <td className="muted">{row.updated_at ?? "-"}</td>
                        <td>
                          <div className="btn-group">
                            <button
                              className="btn btn-sm"
                              onClick={() => {
                                setEditingId(row.chunk_id);
                                setContent(row.content ?? "");
                                setChunkId(row.chunk_id);
                                setSourceRef(row.source_ref);
                                setSubjects((row.subjects ?? []).join(","));
                                setTargetRefs((row.target_refs ?? []).join(","));
                                setObservedAt(row.observed_at ?? "");
                                setExpiresAt(row.expires_at ?? "");
                                setActiveTab("add");
                              }}
                            >
                              <Pencil className="" />
                              编辑
                            </button>
                            <button
                              className="btn btn-sm btn-danger"
                              onClick={() => {
                                if (
                                  window.confirm(
                                    `确定删除知识分块 ${row.chunk_id.slice(0, 20)}？该操作不可撤销。`,
                                  )
                                ) {
                                  remove.mutate(row.chunk_id);
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
          </Panel>
          <Panel
            title="审计记录"
            icon={BookOpen}
            actions={<span className="muted" style={{ fontSize: 12 }}>{(audit.data?.events ?? []).length} 条</span>}
          >
            {(audit.data?.events ?? []).length === 0 ? (
              <p className="muted" style={{ marginBottom: 0 }}>
                暂无知识变更记录。
              </p>
            ) : (
              <div className="table-wrap scroll-panel" style={{ maxHeight: 300 }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>事件</th>
                      <th>Chunk</th>
                      <th>修订</th>
                      <th>时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(audit.data?.events ?? []).map((event) => (
                      <tr key={event.event_id}>
                        <td>
                          <Badge value={event.event_type}>{event.event_type}</Badge>
                        </td>
                        <td className="mono">{event.payload.chunk_id?.slice(0, 20) ?? ""}</td>
                        <td className="num">{event.payload.revision ?? ""}</td>
                        <td className="muted">{event.occurred_at}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
        </>
      ) : null}

      {activeTab === "add" ? (
        <Panel title={editingId ? "编辑知识分块" : "新增知识分块"} icon={FilePlus2}>
          <div className="form-grid">
            <label className="field">
              Chunk id
              <input
                placeholder="留空自动生成"
                value={chunkId}
                disabled={Boolean(editingId)}
                onChange={(event) => setChunkId(event.target.value)}
              />
            </label>
            <label className="field">
              来源 ref
              <input
                value={sourceRef}
                onChange={(event) => setSourceRef(event.target.value)}
              />
            </label>
            <label className="field">
              项目 id（空 = 全局）
              <input
                value={project}
                onChange={(event) => setProject(event.target.value)}
              />
            </label>
            <label className="field">
              主题（逗号分隔）
              <input
                value={subjects}
                onChange={(event) => setSubjects(event.target.value)}
              />
            </label>
            <label className="field">
              目标 refs（逗号分隔，空 = 全局）
              <input
                value={targetRefs}
                onChange={(event) => setTargetRefs(event.target.value)}
                placeholder="https://target.example, 192.168.1.0/24"
              />
            </label>
            <label className="field">
              观测时间（ISO，空 = 现在）
              <input
                value={observedAt}
                onChange={(event) => setObservedAt(event.target.value)}
                placeholder="2026-08-05T00:00:00Z"
              />
            </label>
            <label className="field">
              过期时间（ISO，可选）
              <input
                value={expiresAt}
                onChange={(event) => setExpiresAt(event.target.value)}
                placeholder="2026-09-05T00:00:00Z"
              />
            </label>
          </div>
          <label className="field" style={{ marginTop: 12 }}>
            内容
            <textarea
              rows={6}
              placeholder="knowledge content"
              value={content}
              onChange={(event) => setContent(event.target.value)}
            />
          </label>
          <div className="btn-group" style={{ marginTop: 12 }}>
            <button
              className="btn btn-primary"
              onClick={() => add.mutate()}
              disabled={!content || add.isPending}
              title={
                !content
                  ? "请先输入知识内容"
                  : add.isPending
                    ? "正在提交..."
                    : undefined
              }
            >
              {add.isPending ? "保存中..." : editingId ? "保存修改" : "新增分块"}
            </button>
            {editingId ? (
              <button
                className="btn"
                onClick={() => {
                  setEditingId(null);
                  setContent("");
                  setChunkId("");
                }}
              >
                取消编辑
              </button>
            ) : null}
          </div>
          {add.isError ? <Notice tone="error">{String(add.error)}</Notice> : null}
        </Panel>
      ) : null}

      {activeTab === "import" ? (
        <Panel title="导入 Markdown" icon={Upload}>
          <div className="form-grid">
            <label className="field">
              来源 id
              <input
                value={importSource}
                onChange={(event) => setImportSource(event.target.value)}
              />
            </label>
            <label className="field">
              License
              <input
                value={importLicense}
                onChange={(event) => setImportLicense(event.target.value)}
              />
            </label>
            <label className="field">
              Version
              <input
                value={importVersion}
                onChange={(event) => setImportVersion(event.target.value)}
              />
            </label>
            <label className="field">
              主题（逗号分隔）
              <input
                value={subjects}
                onChange={(event) => setSubjects(event.target.value)}
              />
            </label>
            <label className="field">
              目标 refs（逗号分隔，空 = 全局）
              <input
                value={targetRefs}
                onChange={(event) => setTargetRefs(event.target.value)}
                placeholder="https://target.example"
              />
            </label>
          </div>
          <label className="field" style={{ marginTop: 12 }}>
            Markdown（支持 frontmatter：subjects / graph）
            <textarea
              rows={8}
              placeholder="markdown with optional --- frontmatter"
              value={importMarkdown}
              onChange={(event) => setImportMarkdown(event.target.value)}
            />
          </label>
          <label className="field" style={{ marginTop: 12 }}>
            文件上传（支持 .md / .markdown / .txt / .pdf / .docx）
            <input
              type="file"
              accept=".md,.markdown,.txt,.pdf,.docx"
              onChange={(event) => setImportFile(event.target.files?.[0] ?? null)}
            />
          </label>
          <div className="btn-group" style={{ marginTop: 12 }}>
            <button
              className="btn btn-primary"
              onClick={() => importMarkdownMutation.mutate()}
              disabled={!importMarkdown || importMarkdownMutation.isPending}
              title={
                !importMarkdown
                  ? "请先输入 Markdown 内容"
                  : importMarkdownMutation.isPending
                    ? "正在导入..."
                    : undefined
              }
            >
              {importMarkdownMutation.isPending ? "导入中..." : "导入"}
            </button>
            <button
              className="btn"
              onClick={() => importFileMutation.mutate()}
              disabled={!importFile || importFileMutation.isPending}
              title={
                importFile
                  ? "上传并解析文档"
                  : "请先选择要导入的文件"
              }
            >
              {importFileMutation.isPending ? "解析中..." : "上传并导入"}
            </button>
          </div>
          {importMarkdownMutation.isError ? (
            <Notice tone="error">{String(importMarkdownMutation.error)}</Notice>
          ) : null}
          {importFileMutation.isError ? (
            <Notice tone="error">{String(importFileMutation.error)}</Notice>
          ) : null}
        </Panel>
      ) : null}

      {activeTab === "search" ? (
        <Panel title="知识检索" icon={Search}>
          <div className="toolbar">
            <input
              type="text"
              aria-label="检索查询"
              placeholder="检索查询"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <input
              type="text"
              aria-label="目标范围"
              placeholder="目标 ref（空 = 全局）"
              value={searchTarget}
              onChange={(event) => setSearchTarget(event.target.value)}
            />
            <input
              type="text"
              aria-label="观测起始时间"
              placeholder="观测起始 ISO"
              value={searchSince}
              onChange={(event) => setSearchSince(event.target.value)}
            />
            <input
              type="text"
              aria-label="观测截止时间"
              placeholder="观测截止 ISO"
              value={searchUntil}
              onChange={(event) => setSearchUntil(event.target.value)}
            />
            <button
              className="btn btn-primary"
              disabled={searchLoading || !query.trim()}
              title={
                searchLoading
                  ? "正在检索..."
                  : !query.trim()
                    ? "请输入检索查询"
                    : undefined
              }
              onClick={async () => {
                setSearchLoading(true);
                try {
                  const params = new URLSearchParams({
                    q: query,
                    ...(project ? { project_id: project } : {}),
                    ...(searchTarget ? { target_ref: searchTarget } : {}),
                    ...(searchSince ? { observed_since: searchSince } : {}),
                    ...(searchUntil ? { observed_until: searchUntil } : {}),
                  });
                  const payload = (await control.requestPublic(
                    `/api/v1/knowledge/search?${params.toString()}`,
                  )) as unknown as KnowledgeRow[];
                  setSearchResults(payload.filter((row) => !("excluded" in row)));
                } finally {
                  setSearchLoading(false);
                }
              }}
            >
              <Search className="" />
              {searchLoading ? "检索中..." : "检索"}
            </button>
            <button className="btn" onClick={() => setSearchResults(null)}>
              清除结果
            </button>
          </div>
          {searchResults === null ? (
            <EmptyState title="输入查询开始检索" description="结果会显示匹配的知识分块。" />
          ) : searchResults.length === 0 ? (
            <EmptyState title="没有匹配结果" description="调整查询词后重试。" />
          ) : (
            <div className="card-grid">
              {searchResults.map((row) => (
                <div className="card" key={row.chunk_id}>
                  <div className="card-title">
                    <code>{row.chunk_id}</code>
                  </div>
                  <p className="card-meta">
                    来源 {row.source_ref} · 信任 {row.trust}
                  </p>
                  <p className="muted" style={{ fontSize: 12 }}>
                    {String(row.content ?? "").slice(0, 180)}
                  </p>
                </div>
              ))}
            </div>
          )}
        </Panel>
      ) : null}

      {activeTab === "graph" ? (
        graph.isLoading ? (
          <Loading label="加载知识图谱" />
        ) : graph.isError ? (
          <ErrorBanner message={String(graph.error)} />
        ) : (
          <KnowledgeGraphView graph={graph.data} />
        )
      ) : null}
      {remove.isError ? <Notice tone="error">{String(remove.error)}</Notice> : null}
    </section>
  );
}
