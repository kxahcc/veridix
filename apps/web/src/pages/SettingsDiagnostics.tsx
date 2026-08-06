import { useEffect, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Boxes,
  Cable,
  CheckCircle2,
  Cpu,
  Database,
  GitBranch,
  ListTree,
  PlugZap,
  Play,
  RefreshCw,
  Save,
  Server,
  ServerCog,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
  Wrench,
} from "lucide-react";
import { CONTROL_URL, control } from "../api.js";
import { ErrorBanner, Loading } from "../components/Status.js";
import { Badge, EmptyState, Kpi, Notice, Panel } from "../components/ui.js";
import { JsonImportForm } from "../components/JsonImportForm.js";
import { MarkdownView } from "../components/MarkdownView.js";

type TabId =
  | "overview"
  | "providers"
  | "runners"
  | "skills"
  | "mcp"
  | "tools"
  | "templates"
  | "loops"
  | "audit"
  | "retrieval";

const TABS: Array<{ id: TabId; label: string; icon: typeof Cpu }> = [
  { id: "overview", label: "概览", icon: ServerCog },
  { id: "providers", label: "模型供应商", icon: Sparkles },
  { id: "runners", label: "执行单元", icon: Server },
  { id: "skills", label: "技能", icon: Boxes },
  { id: "mcp", label: "MCP", icon: PlugZap },
  { id: "tools", label: "工具", icon: Wrench },
  { id: "templates", label: "角色模板", icon: ListTree },
  { id: "loops", label: "Loop Profiles", icon: GitBranch },
  { id: "audit", label: "审计日志", icon: ShieldCheck },
  { id: "retrieval", label: "检索与存储", icon: Database },
];

function parseJson(text: string, fallback: Record<string, unknown>) {
  try {
    const value = JSON.parse(text || "{}");
    return value && typeof value === "object" ? value : fallback;
  } catch {
    return fallback;
  }
}

const SMOKE_VERIFIED_TOOLS = new Set([
  "web.ffuf.scan",
  "web.directory.brute",
  "nuclei.scan",
  "web.whatweb.scan",
  "web.dirsearch.scan",
  "web.wfuzz",
  "web.wpscan.scan",
  "host.auth.hydra",
  "network.dns.recon",
  "network.subfinder.scan",
  "network.httpx.probe",
  "network.naabu.scan",
]);

function RegistryForm({
  fields,
  values,
  onChange,
  onSubmit,
  pending,
  submitLabel,
  summaryLabel = "新增登记",
}: {
  fields: Array<{ key: string; label: string; placeholder?: string }>;
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
  onSubmit: () => void;
  pending: boolean;
  submitLabel: string;
  summaryLabel?: string;
}) {
  return (
    <details className="registry-form">
      <summary>{summaryLabel}</summary>
      <div className="form-grid" style={{ marginTop: 10 }}>
        {fields.map((field) => (
          <label className="field" key={field.key}>
            {field.label}
            <input
              value={values[field.key] ?? ""}
              onChange={(event) => onChange(field.key, event.target.value)}
              placeholder={field.placeholder ?? ""}
            />
          </label>
        ))}
      </div>
      <button
        className="btn btn-primary"
        onClick={onSubmit}
        disabled={pending}
        style={{ marginTop: 10 }}
      >
        {pending ? "提交中..." : submitLabel}
      </button>
    </details>
  );
}

function roleLabel(role: unknown): string {
  if (typeof role === "string") {
    return role;
  }
  const row = (role ?? {}) as Record<string, unknown>;
  return String(row.role_id ?? row.name ?? row.role ?? "?");
}

function RegistryTable({
  rows,
  columns,
  actions,
}: {
  rows: Array<Record<string, unknown>>;
  columns: Array<{ key: string; label: string }>;
  actions?: (row: Record<string, unknown>) => ReactNode;
}) {
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key}>{column.label}</th>
            ))}
            {actions ? <th>操作</th> : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column.key} className="mono">
                  {String(row[column.key] ?? "-")}
                </td>
              ))}
              {actions ? <td>{actions(row)}</td> : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SettingsDiagnostics() {
  const queryClient = useQueryClient();
  const skillDetailRef = useRef<HTMLDivElement | null>(null);
  const [tab, setTab] = useState<TabId>("overview");
  const [selfCheck, setSelfCheck] = useState<
    Record<string, unknown> | null
  >(null);
  const [selfCheckLoading, setSelfCheckLoading] = useState(false);
  const [selfCheckError, setSelfCheckError] = useState("");
  const [providerForm, setProviderForm] = useState<Record<string, string>>({
    provider_id: "",
    model: "",
    endpoint: "",
    api_key_ref: "",
    backend: "openai",
    litellm_provider: "",
    timeout_seconds: "5",
    thinking_mode: "",
    reasoning_effort: "none",
    retries: "5",
    streaming: "false",
    max_tokens: "",
    headers: "{}",
  });
  const [providerModelOptions, setProviderModelOptions] = useState<string[]>([]);
  const [toolSearch, setToolSearch] = useState("");
  const [probeResults, setProbeResults] = useState<
    Record<string, Record<string, unknown>>
  >({});
  const [mcpTestResults, setMcpTestResults] = useState<
    Record<string, Record<string, unknown>>
  >({});
  const [retrievalForm, setRetrievalForm] = useState<Record<string, string>>({
    embedding_backend: "openai_compatible",
    embedding_endpoint: "",
    embedding_model: "",
    embedding_api_key_ref: "",
    vector_store: "pgvector",
    vector_url: "",
    vector_database_url: "",
    vector_collection: "veridix_chunks",
    graph_backend: "neo4j",
    graph_uri: "",
    graph_user: "neo4j",
    graph_password: "",
    rerank_enabled: "false",
    rerank_backend: "fastembed",
    rerank_endpoint: "",
    rerank_model: "",
    fusion: "rrf",
    deadline_seconds: "8",
  });
  const [retrievalProbe, setRetrievalProbe] = useState<
    Record<string, Record<string, unknown>>
  >({});
  const [runnerForm, setRunnerForm] = useState<Record<string, string>>({
    runner_id: "agent-worker",
    kind: "control-plane",
  });
  const [skillForm, setSkillForm] = useState<Record<string, string>>({
    skill_ref: "",
    name: "",
    version: "1",
    trigger: "",
    runner: "",
    risk_level: "L1",
    status: "available",
  });
  const [skillPackageFile, setSkillPackageFile] = useState<File | null>(null);
  const [skillMarkdown, setSkillMarkdown] = useState("");
  const [skillImportResult, setSkillImportResult] = useState<
    Record<string, unknown> | null
  >(null);
  const [skillImportError, setSkillImportError] = useState("");
  const [skillImportPending, setSkillImportPending] = useState(false);
  const [mcpForm, setMcpForm] = useState<Record<string, string>>({
    server_id: "",
    name: "",
    kind: "local",
    command: "",
    description: "",
    timeout_seconds: "10",
    env: "{}",
    status: "available",
  });
  const [toolForm, setToolForm] = useState<Record<string, string>>({
    tool_ref: "",
    capability: "",
    status: "available",
  });
  const [templateForm, setTemplateForm] = useState<Record<string, string>>({
    template_id: "",
    label: "",
    description: "",
    roles: "",
  });
  const [templateDetail, setTemplateDetail] = useState<
    Record<string, unknown> | null
  >(null);
  const diagnostics = useQuery({
    queryKey: ["diagnostics"],
    queryFn: () => control.requestPublic("/api/v1/diagnostics"),
    refetchInterval: 5000,
  });
  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: () => control.listProviders(),
    refetchInterval: 5000,
  });
  const providerDefault = useQuery({
    queryKey: ["provider-default"],
    queryFn: () => control.getProviderDefault(),
    refetchInterval: 8000,
  });
  const runners = useQuery({
    queryKey: ["runners"],
    queryFn: () => control.listRunners(),
    refetchInterval: 5000,
  });
  const skills = useQuery({
    queryKey: ["runtime-skills"],
    queryFn: () => control.listSkills(),
  });
  const mcp = useQuery({
    queryKey: ["runtime-mcp"],
    queryFn: () => control.requestPublic("/api/v1/runtime/mcp"),
  });
  const mcpPresets = useQuery({
    queryKey: ["mcp-presets"],
    queryFn: () => control.listMcpPresets(),
  });
  const tools = useQuery({
    queryKey: ["runtime-tools"],
    queryFn: () => control.requestPublic("/api/v1/runtime/tools"),
  });
  const toolPacks = useQuery({
    queryKey: ["tool-packs"],
    queryFn: () => control.listToolPacks(),
    refetchInterval: 8000,
  });
  const auditLogs = useQuery({
    queryKey: ["audit-logs"],
    queryFn: () => control.listAuditLogs({ limit: 100 }),
    refetchInterval: 8000,
  });
  const roleTemplates = useQuery({
    queryKey: ["role-templates"],
    queryFn: () => control.listRoleTemplates(),
    refetchInterval: 8000,
  });
  const loopProfiles = useQuery({
    queryKey: ["loop-profiles"],
    queryFn: () => control.listLoopProfiles(),
    refetchInterval: 8000,
  });
  const presets = useQuery({
    queryKey: ["provider-presets"],
    queryFn: () => control.listProviderPresets(),
  });
  const retrieval = useQuery({
    queryKey: ["retrieval-settings"],
    queryFn: () => control.getRetrievalSettings(),
    refetchInterval: 8000,
  });
  const registerProvider = useMutation({
    mutationFn: () =>
      control.registerProvider({
        provider_id: providerForm.provider_id,
        model: providerForm.model,
        endpoint: providerForm.endpoint,
        api_key_ref: providerForm.api_key_ref || undefined,
        backend: providerForm.backend,
        litellm_provider: providerForm.litellm_provider || undefined,
        timeout_seconds: Number(providerForm.timeout_seconds) || undefined,
        thinking_mode: providerForm.thinking_mode || undefined,
        reasoning_effort:
          providerForm.reasoning_effort === "none"
            ? undefined
            : providerForm.reasoning_effort,
        retries: Number(providerForm.retries) || undefined,
        streaming: providerForm.streaming === "true",
        max_tokens: Number(providerForm.max_tokens) || undefined,
        headers: parseJson(providerForm.headers, {}),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
      setProviderForm({
        provider_id: "",
        model: "",
        endpoint: "",
        api_key_ref: "",
        backend: "openai",
        litellm_provider: "",
        timeout_seconds: "5",
        thinking_mode: "",
        reasoning_effort: "none",
        retries: "5",
        streaming: "false",
        max_tokens: "",
        headers: "{}",
      });
      setProviderModelOptions([]);
    },
  });
  const deleteProvider = useMutation({
    mutationFn: (id: string) => control.deleteProvider(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
  });
  const probeProvider = useMutation({
    mutationFn: (row: Record<string, unknown>) =>
      control.probeProvider({
        provider_id: String(row.provider_id),
        endpoint: String(row.endpoint),
        model: String(row.model),
        api_key_ref:
          String((row.config as Record<string, unknown> | undefined)?.api_key_ref ?? "") ||
          undefined,
        backend:
          String((row.config as Record<string, unknown> | undefined)?.backend ?? "openai"),
        litellm_provider:
          String((row.config as Record<string, unknown> | undefined)?.litellm_provider ?? ""),
        timeout_seconds: Number(
          (row.config as Record<string, unknown> | undefined)?.timeout_seconds ?? 5,
        ),
      }),
    onSuccess: (result, variables) => {
      setProbeResults((current) => ({
        ...current,
        [String(variables.provider_id)]: result,
      }));
    },
  });
  const fetchModels = useMutation({
    mutationFn: () =>
      control.fetchProviderModels({
        provider_id: providerForm.provider_id || "probe",
        endpoint: providerForm.endpoint,
        model: providerForm.model,
        api_key_ref: providerForm.api_key_ref || undefined,
        backend: providerForm.backend,
        litellm_provider: providerForm.litellm_provider || undefined,
        timeout_seconds: Number(providerForm.timeout_seconds) || 5,
      }),
    onSuccess: (result) => {
      const models = (result.models as string[] | undefined) ?? [];
      setProviderModelOptions(models);
      if (models.length > 0 && !models.includes(providerForm.model)) {
        setProviderForm((current) => ({ ...current, model: models[0] }));
      }
    },
  });
  const setDefault = useMutation({
    mutationFn: (row: Record<string, unknown>) =>
      control.setProviderDefault({
        provider_id: String(row.provider_id),
        endpoint: String(row.endpoint),
        model: String(row.model),
        api_key_ref: providerForm.api_key_ref || undefined,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["provider-default"] });
    },
  });
  const registerRunner = useMutation({
    mutationFn: () =>
      control.registerRunner({
        runner_id: runnerForm.runner_id,
        kind: runnerForm.kind,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["runners"] });
      setRunnerForm({ runner_id: "", kind: "control-plane" });
    },
  });
  const registerSkill = useMutation({
    mutationFn: () => control.registerSkill(skillForm),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["runtime-skills"] });
      setSkillForm({ ...skillForm, skill_ref: "", name: "" });
    },
  });
  const importSkillPackage = useMutation({
    mutationFn: async () => {
      if (!skillPackageFile && !skillMarkdown.trim()) {
        throw new Error("请选择 .zip 技能包或填写 SKILL.md 内容");
      }
      const form = new FormData();
      if (skillPackageFile) {
        form.append("file", skillPackageFile);
      } else {
        form.append("skill_md", skillMarkdown);
      }
      form.append("overwrite", "true");
      const response = await fetch(
        `${CONTROL_URL}/api/v1/runtime/skills/import-package`,
        { method: "POST", body: form },
      );
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `HTTP ${response.status}`);
      }
      return response.json() as Promise<Record<string, unknown>>;
    },
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ["runtime-skills"] });
      setSkillImportResult(result);
      setSkillPackageFile(null);
      setSkillMarkdown("");
      setSkillImportError("");
    },
    onError: (error) => {
      setSkillImportError(String(error));
    },
  });
  const registerMcp = useMutation({
    mutationFn: () =>
      control.registerMcp({
        ...mcpForm,
        env: parseJson(mcpForm.env, {}),
        timeout_seconds: Number(mcpForm.timeout_seconds) || undefined,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["runtime-mcp"] });
      setMcpForm({
        server_id: "",
        name: "",
        kind: "local",
        command: "",
        description: "",
        timeout_seconds: "10",
        env: "{}",
        status: "available",
      });
    },
  });
  const registerTool = useMutation({
    mutationFn: () => control.registerTool(toolForm),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["runtime-tools"] });
      setToolForm({ ...toolForm, tool_ref: "", capability: "" });
    },
  });
  const deleteSkill = useMutation({
    mutationFn: (id: string) => control.deleteSkill(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["runtime-skills"] });
    },
  });
  const openSkill = useMutation({
    mutationFn: (ref: string) => control.getSkill(ref),
  });
  useEffect(() => {
    if (openSkill.isSuccess) {
      skillDetailRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    }
  }, [openSkill.isSuccess, openSkill.data]);
  const deleteMcp = useMutation({
    mutationFn: (id: string) => control.deleteMcp(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["runtime-mcp"] });
    },
  });
  const testMcp = useMutation({
    mutationFn: (id: string) => control.testMcp(id),
    onSuccess: (result, id) => {
      setMcpTestResults((current) => ({ ...current, [id]: result }));
    },
  });
  const deleteTool = useMutation({
    mutationFn: (id: string) => control.deleteTool(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["runtime-tools"] });
    },
  });
  const buildRetrievalPayload = () => ({
    embedding: {
      backend: retrievalForm.embedding_backend,
      endpoint: retrievalForm.embedding_endpoint,
      model: retrievalForm.embedding_model,
      api_key_ref: retrievalForm.embedding_api_key_ref || undefined,
    },
    vector_store: {
      type: retrievalForm.vector_store,
      url: retrievalForm.vector_url || undefined,
      database_url: retrievalForm.vector_database_url || undefined,
      collection: retrievalForm.vector_collection || undefined,
    },
    graph: {
      backend: retrievalForm.graph_backend,
      uri: retrievalForm.graph_uri || undefined,
      user: retrievalForm.graph_user || undefined,
      password: retrievalForm.graph_password || undefined,
    },
    rerank: {
      enabled: retrievalForm.rerank_enabled === "true",
      backend: retrievalForm.rerank_backend,
      endpoint: retrievalForm.rerank_endpoint || undefined,
      model: retrievalForm.rerank_model,
    },
    fusion: retrievalForm.fusion,
    deadline_seconds: Number(retrievalForm.deadline_seconds) || 8,
  });
  const applyMatureRetrievalDefaults = () => {
    setRetrievalForm({
      embedding_backend: "openai_compatible",
      embedding_endpoint: "http://127.0.0.1:11434/v1",
      embedding_model: "nomic-embed-text",
      embedding_api_key_ref: "",
      vector_store: "qdrant",
      vector_url: "http://127.0.0.1:6333",
      vector_database_url: "",
      vector_collection: "veridix_chunks",
      graph_backend: "neo4j",
      graph_uri: "bolt://127.0.0.1:7687",
      graph_user: "neo4j",
      graph_password: "veridixpass",
      rerank_enabled: "true",
      rerank_backend: "fastembed",
      rerank_endpoint: "",
      rerank_model: "BAAI/bge-reranker-base",
      fusion: "rrf",
      deadline_seconds: "8",
    });
  };
  const saveRetrieval = useMutation({
    mutationFn: () => control.setRetrievalSettings(buildRetrievalPayload()),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["retrieval-settings"] });
    },
  });
  const testRetrieval = useMutation({
    mutationFn: () => control.testRetrievalSettings(buildRetrievalPayload()),
    onSuccess: (result) => {
      setRetrievalProbe(result as Record<string, Record<string, unknown>>);
    },
  });
  const saveTemplate = useMutation({
    mutationFn: async () => {
      let roles: unknown[] = [];
      try {
        roles = JSON.parse(templateForm.roles || "[]");
      } catch {
        throw new Error("roles 必须是 JSON 数组");
      }
      return control.saveRoleTemplate({
        template_id: templateForm.template_id,
        label: templateForm.label,
        description: templateForm.description,
        roles,
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["role-templates"] });
      setTemplateForm({
        template_id: "",
        label: "",
        description: "",
        roles: "",
      });
    },
  });
  const deleteTemplate = useMutation({
    mutationFn: (id: string) => control.deleteRoleTemplate(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["role-templates"] });
    },
  });
  const data = diagnostics.data as Record<string, unknown> | undefined;
  const toolEnvironment = data?.tool_environment as
    | { available?: boolean; digest?: string; packs?: string[] }
    | undefined;
  const connectors = (data?.connectors as
    | Record<string, { url: string; status: string }>
    | undefined) ?? {};
  const storage = (data?.storage as
    | (Record<string, Record<string, string>> & { available?: boolean })
    | undefined) ?? {};
  const connectorOk = Object.values(connectors).filter(
    (value) => value.status === "ok",
  ).length;
  const storageBackendEntries = Object.entries(storage).filter(
    ([key]) => key !== "available",
  );
  const components = (data?.components as
    | Record<string, Record<string, unknown>>
    | undefined) ?? {};
  const componentEntries = Object.entries(components);
  const storageOk = storageBackendEntries.filter(
    ([, value]) =>
      !String((value as Record<string, string>).status ?? "").includes("error") &&
      !String((value as Record<string, string>).status ?? "").includes("failed") &&
      !String((value as Record<string, string>).status ?? "").includes("not_configured"),
  ).length;
  const providerRows = (providers.data ?? []) as Array<Record<string, unknown>>;
  const skillRows = (skills.data ?? []) as Array<Record<string, unknown>>;
  const toolPackRows = (toolPacks.data ?? []) as Array<Record<string, unknown>>;
  const allTools = toolPackRows.flatMap((pack) =>
    (
      (pack.tools as Array<Record<string, unknown>> | undefined) ?? []
    ).map((tool) => ({ ...tool, pack: String(pack.name ?? "") }) as Record<string, unknown>),
  );
  const toolQuery = toolSearch.trim().toLowerCase();
  const filteredTools = toolQuery
    ? allTools.filter((tool) =>
        [
          String(tool.ref ?? ""),
          String(tool.name ?? ""),
          String(tool.capability ?? ""),
          String(tool.pack ?? ""),
          String(tool.description ?? ""),
        ]
          .join(" ")
          .toLowerCase()
          .includes(toolQuery),
      )
    : allTools;
  const mcpPresetRows = (mcpPresets.data ?? []) as Array<Record<string, unknown>>;
  const auditRows = (auditLogs.data ?? []) as Array<Record<string, unknown>>;
  const defaultId = String(
    (providerDefault.data as Record<string, unknown> | null)?.provider_id ?? "",
  );
  const templateRows = (roleTemplates.data ?? []) as Array<Record<string, unknown>>;
  const loopProfileRows = Object.values(
    (loopProfiles.data ?? {}) as Record<string, Record<string, unknown>>,
  ).map((profile) => ({
    name: String(profile.name ?? ""),
    version: String(profile.version ?? ""),
    category: String(profile.category ?? ""),
    oracle: String(profile.oracle ?? ""),
    success: String(profile.success_criteria ?? ""),
    risk: String(profile.risk_level ?? ""),
    sandbox: String(profile.sandbox_profile ?? ""),
    evidence: Array.isArray(profile.evidence_requirements)
      ? (profile.evidence_requirements as string[]).join(", ")
      : "",
    knowledge: Array.isArray(profile.knowledge_query)
      ? (profile.knowledge_query as string[]).join(", ")
      : "",
  }));
  const presetRows = (presets.data ?? []) as Array<Record<string, unknown>>;
  useEffect(() => {
    const data = retrieval.data as
      | {
          embedding?: {
            backend?: string;
            endpoint?: string;
            model?: string;
            api_key_ref?: string;
          };
          vector_store?: {
            type?: string;
            url?: string;
            database_url?: string;
            collection?: string;
          };
          graph?: {
            backend?: string;
            uri?: string;
            user?: string;
            password?: string;
          };
          rerank?: {
            enabled?: boolean;
            backend?: string;
            endpoint?: string;
            model?: string;
          };
          fusion?: string;
          deadline_seconds?: number;
        }
      | null
      | undefined;
    if (!data) {
      return;
    }
    setRetrievalForm({
      embedding_backend: String(data.embedding?.backend ?? "openai_compatible"),
      embedding_endpoint: String(data.embedding?.endpoint ?? ""),
      embedding_model: String(data.embedding?.model ?? ""),
      embedding_api_key_ref: String(data.embedding?.api_key_ref ?? ""),
      vector_store: String(data.vector_store?.type ?? "pgvector"),
      vector_url: String(data.vector_store?.url ?? ""),
      vector_database_url: String(data.vector_store?.database_url ?? ""),
      vector_collection: String(data.vector_store?.collection ?? "veridix_chunks"),
      graph_backend: String(data.graph?.backend ?? "neo4j"),
      graph_uri: String(data.graph?.uri ?? ""),
      graph_user: String(data.graph?.user ?? "neo4j"),
      graph_password: String(data.graph?.password ?? ""),
      rerank_enabled: data.rerank?.enabled ? "true" : "false",
      rerank_backend: String(data.rerank?.backend ?? "fastembed"),
      rerank_endpoint: String(data.rerank?.endpoint ?? ""),
      rerank_model: String(data.rerank?.model ?? ""),
      fusion: String(data.fusion ?? "rrf"),
      deadline_seconds: String(data.deadline_seconds ?? 8),
    });
  }, [retrieval.data]);

  return (
    <section>
      <header className="page-head">
        <div className="page-head-copy">
          <p className="page-eyebrow">System Management</p>
          <h1>诊断与设置</h1>
          <p className="page-sub">
            管理模型供应商、执行单元、技能、MCP 与工具注册，并查看系统健康。
          </p>
        </div>
        <div className="actions">
          <button
            className="btn"
            disabled={selfCheckLoading}
            onClick={() => {
              setSelfCheckLoading(true);
              setSelfCheckError("");
              void control
                .requestJson(
                  "POST",
                  "/api/v1/diagnostics/self-check",
                )
                .then((result) => {
                  setSelfCheck(result as Record<string, unknown>);
                })
                .catch((error: unknown) => {
                  setSelfCheckError(String(error));
                })
                .finally(() => {
                  setSelfCheckLoading(false);
                });
            }}
          >
            <Play className="" />
            {selfCheckLoading ? "检测中..." : "环境自检"}
          </button>
          <button
            className="btn"
            onClick={() => {
              void diagnostics.refetch();
              void providers.refetch();
              void providerDefault.refetch();
              void runners.refetch();
            }}
          >
            <RefreshCw className="" />
            刷新
          </button>
        </div>
      </header>
      <div className="kpi-grid">
        <Kpi
          label="工具环境"
          value={toolEnvironment?.available ? "可用" : "未就绪"}
          tone={toolEnvironment?.available ? "ok" : "warn"}
          note={toolEnvironment?.digest?.slice(0, 16) ?? "无 digest"}
        />
        <Kpi
          label="连接器"
          value={`${connectorOk}/${Object.keys(connectors).length}`}
          tone={connectorOk === Object.keys(connectors).length ? "ok" : "warn"}
          note="模型 / 工具环境连接"
        />
        <Kpi
          label="存储后端"
          value={`${storageOk}/${storageBackendEntries.length}`}
          tone={storageOk === storageBackendEntries.length ? "ok" : "warn"}
          note="向量 / 图 / 重排"
        />
        <Kpi
          label="供应商"
          value={providerRows.length}
          tone="info"
          note={defaultId ? `默认 ${defaultId}` : "未设置默认"}
        />
      </div>
      <div className="tabs">
        {TABS.map((item) => (
          <button
            key={item.id}
            className={`tab${tab === item.id ? " active" : ""}`}
            onClick={() => setTab(item.id)}
          >
            <item.icon className="" />
            {item.label}
          </button>
        ))}
      </div>

      {tab === "overview" ? (
        <>
          {selfCheck ? (
            <Panel
              title="环境自检"
              icon={CheckCircle2}
              actions={
                <Badge
                  value={selfCheck.ok ? "ok" : "warn"}
                >
                  {selfCheck.ok ? "通过" : "有异常"}
                </Badge>
              }
            >
              <div className="memory-summary">
                <span>Worker {String(selfCheck.worker ?? "unknown")}</span>
                <span>
                  供应商 {String((selfCheck.counts as Record<string, number>)?.providers ?? 0)}
                </span>
                <span>
                  MCP {String((selfCheck.counts as Record<string, number>)?.mcp ?? 0)}
                </span>
                <span>
                  Runner {String((selfCheck.counts as Record<string, number>)?.runners ?? 0)}
                </span>
              </div>
              <div className="card-grid">
                {Object.entries(
                  (selfCheck.components as Record<string, Record<string, string>>) ?? {},
                ).map(([key, value]) => (
                  <div className="card" key={key}>
                    <div className="panel-head" style={{ marginBottom: 4 }}>
                      <div className="card-title" style={{ margin: 0 }}>
                        <code>{key}</code>
                      </div>
                      <Badge value={value.status ?? "unknown"}>
                        {value.status ?? "unknown"}
                      </Badge>
                    </div>
                    <p className="card-meta" style={{ margin: 0 }}>
                      {String(value.detail ?? "")}
                    </p>
                  </div>
                ))}
              </div>
            </Panel>
          ) : null}
          {selfCheckError ? (
            <Notice tone="error">{selfCheckError}</Notice>
          ) : null}
          <Panel title="组件健康" icon={ServerCog}>
            <div className="card-grid">
              {componentEntries.map(([key, value]) => (
                <div className="card" key={key}>
                  <div className="panel-head" style={{ marginBottom: 4 }}>
                    <div className="card-title" style={{ margin: 0 }}>
                      <code>{key}</code>
                    </div>
                    <Badge value={String(value.status ?? "unknown")}>
                      {String(value.status ?? "unknown")}
                    </Badge>
                  </div>
                  <p className="muted" style={{ fontSize: 12 }}>
                    {String(value.detail ?? "")}
                  </p>
                </div>
              ))}
            </div>
          </Panel>
          <Panel title="工具环境" icon={Boxes}>
            {toolEnvironment?.available ? (
              <>
                <p className="muted" style={{ wordBreak: "break-all" }}>
                  digest <code>{toolEnvironment.digest}</code>
                </p>
                <div className="card-grid">
                  {(toolEnvironment.packs ?? []).map((pack) => (
                    <div className="card" key={pack}>
                      <div className="card-title">
                        <code>{pack}</code>
                      </div>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <EmptyState
                title="工具环境未就绪"
                description="运行 worker 后生成工具环境快照。"
                action={
                  <button
                    className="btn btn-sm"
                    onClick={() => void diagnostics.refetch()}
                  >
                    刷新
                  </button>
                }
              />
            )}
          </Panel>
          <Panel
            title="连接器"
            icon={Cable}
            actions={
              <span className="muted" style={{ fontSize: 12 }}>
                {connectorOk}/{Object.keys(connectors).length}
              </span>
            }
          >
            {Object.keys(connectors).length === 0 ? (
              <EmptyState
                title="暂无连接器"
                description="连接器注册后显示。"
                action={
                  <button
                    className="btn btn-sm"
                    onClick={() => void diagnostics.refetch()}
                  >
                    刷新
                  </button>
                }
              />
            ) : (
              <div className="stack" style={{ gap: 8 }}>
                {Object.entries(connectors).map(([name, value]) => (
                  <div className="card" key={name}>
                    <div className="panel-head" style={{ marginBottom: 4 }}>
                      <div className="card-title" style={{ margin: 0 }}>
                        {name}
                      </div>
                      <Badge value={value.status}>{value.status}</Badge>
                    </div>
                    <p className="card-meta" style={{ margin: 0 }}>
                      {value.url || "未配置"}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </Panel>
          <Panel title="存储后端" icon={Database}>
            <div className="card-grid">
              {storageBackendEntries.map(([key, value]) => (
                <div className="card" key={key}>
                  <div className="panel-head" style={{ marginBottom: 4 }}>
                    <div className="card-title" style={{ margin: 0 }}>
                      <code>{key}</code>
                    </div>
                    <Badge value={String((value as Record<string, string>).status ?? "ok")}>
                      {String((value as Record<string, string>).status ?? "ok")}
                    </Badge>
                  </div>
                  <pre className="result-box" style={{ maxHeight: 140, fontSize: 11 }}>
                    {JSON.stringify(value, null, 2)}
                  </pre>
                </div>
              ))}
            </div>
          </Panel>
          <Panel title="原始诊断" icon={ServerCog}>
            <details className="result-box">
              <summary>Raw diagnostics JSON</summary>
              <pre>{JSON.stringify(data, null, 2)}</pre>
            </details>
          </Panel>
        </>
      ) : null}

      {tab === "providers" ? (
        <Panel
          title="模型供应商"
          icon={Sparkles}
          actions={
            <Badge value={defaultId ? "default" : "none"}>
              {defaultId ? `默认 ${defaultId}` : "未设置默认"}
            </Badge>
          }
        >
          <Notice tone="info">
            任务未单独指定供应商时，Worker 将使用这里的默认供应商，集中管理便于切换模型。
          </Notice>
          <div
            id="provider-form"
            className="panel"
            style={{ marginBottom: 12 }}
          >
            <div className="form-section-title">供应商配置</div>
            <label className="field" style={{ marginBottom: 10 }}>
              常用供应商
              <select
                value=""
                onChange={(event) => {
                  const preset = presetRows.find(
                    (item) => item.id === event.target.value,
                  );
                  if (!preset) {
                    return;
                  }
                  const models = (preset.models as unknown[]) ?? [];
                  setProviderModelOptions(models.map(String));
                  setProviderForm((current) => ({
                    ...current,
                    provider_id: String(preset.id),
                    endpoint: String(preset.endpoint),
                    model: models.length
                      ? String(models[0])
                      : current.model,
                  }));
                }}
              >
                <option value="">选择常用供应商...</option>
                {presetRows.map((preset) => (
                  <option key={String(preset.id)} value={String(preset.id)}>
                    {String(preset.name)} · {String(preset.endpoint)}
                    {preset.local ? " · 本地" : ""}
                  </option>
                ))}
              </select>
            </label>
            <div className="form-grid">
              <label className="field">
                Provider id
                <input
                  value={providerForm.provider_id}
                  onChange={(event) =>
                    setProviderForm((current) => ({
                      ...current,
                      provider_id: event.target.value,
                    }))
                  }
                  placeholder="openai"
                />
              </label>
              <label className="field">
                后端
                <select
                  value={providerForm.backend}
                  onChange={(event) =>
                    setProviderForm((current) => ({
                      ...current,
                      backend: event.target.value,
                    }))
                  }
                >
                  <option value="openai">OpenAI-compatible</option>
                  <option value="litellm">LiteLLM</option>
                </select>
              </label>
              {providerForm.backend === "litellm" ? (
                <label className="field">
                  LiteLLM provider
                  <input
                    value={providerForm.litellm_provider}
                    onChange={(event) =>
                      setProviderForm((current) => ({
                        ...current,
                        litellm_provider: event.target.value,
                      }))
                    }
                    placeholder="deepseek / anthropic / ollama"
                  />
                </label>
              ) : null}
              <label className="field">
                模型
                {providerModelOptions.length > 0 ? (
                  <select
                    value={providerForm.model}
                    onChange={(event) =>
                      setProviderForm((current) => ({
                        ...current,
                        model: event.target.value,
                      }))
                    }
                  >
                    <option value="">选择模型...</option>
                    {providerModelOptions.map((model) => (
                      <option key={model} value={model}>
                        {model}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    value={providerForm.model}
                    onChange={(event) =>
                      setProviderForm((current) => ({
                        ...current,
                        model: event.target.value,
                      }))
                    }
                    placeholder="gpt-4o"
                  />
                )}
                <button
                  className="btn btn-sm"
                  style={{ marginTop: 6 }}
                  onClick={() => fetchModels.mutate()}
                  disabled={fetchModels.isPending || !providerForm.endpoint}
                >
                  {fetchModels.isPending ? "获取中..." : "获取模型列表"}
                </button>
                {fetchModels.isError ? (
                  <span className="muted" style={{ color: "#fca5a5" }}>
                    获取失败：{String(fetchModels.error)}
                  </span>
                ) : null}
              </label>
              <label className="field" style={{ gridColumn: "1 / -1" }}>
                Base URL
                <input
                  value={providerForm.endpoint}
                  onChange={(event) =>
                    setProviderForm((current) => ({
                      ...current,
                      endpoint: event.target.value,
                    }))
                  }
                  placeholder="https://api.openai.com/v1"
                />
              </label>
              <label className="field">
                API key ref
                <input
                  value={providerForm.api_key_ref}
                  onChange={(event) =>
                    setProviderForm((current) => ({
                      ...current,
                      api_key_ref: event.target.value,
                    }))
                  }
                  placeholder="env:OPENAI_API_KEY"
                />
              </label>
              <label className="field">
                超时（秒）
                <input
                  type="number"
                  min={1}
                  value={providerForm.timeout_seconds}
                  onChange={(event) =>
                    setProviderForm((current) => ({
                      ...current,
                      timeout_seconds: event.target.value,
                    }))
                  }
                />
              </label>
              <label className="field">
                thinking mode
                <input
                  value={providerForm.thinking_mode}
                  onChange={(event) =>
                    setProviderForm((current) => ({
                      ...current,
                      thinking_mode: event.target.value,
                    }))
                  }
                  placeholder="high / low"
                />
              </label>
              <label className="field">
                推理强度
                <select
                  value={providerForm.reasoning_effort}
                  onChange={(event) =>
                    setProviderForm((current) => ({
                      ...current,
                      reasoning_effort: event.target.value,
                    }))
                  }
                >
                  <option value="none">不指定</option>
                  <option value="low">low</option>
                  <option value="medium">medium</option>
                  <option value="high">high</option>
                </select>
              </label>
              <label className="field">
                失败重试次数
                <input
                  type="number"
                  min={0}
                  max={10}
                  value={providerForm.retries}
                  onChange={(event) =>
                    setProviderForm((current) => ({
                      ...current,
                      retries: event.target.value,
                    }))
                  }
                />
              </label>
              <label className="field">
                max tokens
                <input
                  type="number"
                  min={1}
                  value={providerForm.max_tokens}
                  onChange={(event) =>
                    setProviderForm((current) => ({
                      ...current,
                      max_tokens: event.target.value,
                    }))
                  }
                />
              </label>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={providerForm.streaming === "true"}
                  onChange={(event) =>
                    setProviderForm((current) => ({
                      ...current,
                      streaming: event.target.checked ? "true" : "false",
                    }))
                  }
                />
                流式响应
              </label>
              <label className="field" style={{ gridColumn: "1 / -1" }}>
                额外请求头（JSON）
                <input
                  value={providerForm.headers}
                  onChange={(event) =>
                    setProviderForm((current) => ({
                      ...current,
                      headers: event.target.value,
                    }))
                  }
                  placeholder='{"X-Project":"veridix"}'
                />
              </label>
            </div>
            <div className="btn-group" style={{ marginTop: 10 }}>
              <button
                className="btn btn-primary"
                onClick={() => registerProvider.mutate()}
                disabled={
                  registerProvider.isPending ||
                  !providerForm.provider_id ||
                  !providerForm.model ||
                  !providerForm.endpoint
                }
              >
                <Save className="" />
                {registerProvider.isPending ? "保存中..." : "保存供应商"}
              </button>
              <button
                className="btn"
                onClick={() =>
                  setProviderForm({
                    provider_id: "",
                    model: "",
                    endpoint: "",
                    api_key_ref: "",
                    backend: "openai",
                    litellm_provider: "",
                    timeout_seconds: "5",
                    thinking_mode: "",
                    streaming: "false",
                    max_tokens: "",
                    headers: "{}",
                  })
                }
              >
                清空表单
              </button>
            </div>
            {registerProvider.isError ? (
              <div style={{ marginTop: 10 }}>
                <Notice tone="error">{String(registerProvider.error)}</Notice>
              </div>
            ) : null}
          </div>
          {providerRows.length === 0 ? (
            <EmptyState
              title="暂无供应商"
              description="登记第一个模型供应商。"
              action={
                <button
                  className="btn btn-sm"
                  onClick={() =>
                    document
                      .getElementById("provider-form")
                      ?.scrollIntoView({ behavior: "smooth" })
                  }
                >
                  登记供应商
                </button>
              }
            />
          ) : (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Provider</th>
                    <th>模型</th>
                    <th>Base URL</th>
                    <th>状态</th>
                    <th>默认</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {providerRows.map((row) => {
                    const config = (row.config ?? {}) as Record<string, unknown>;
                    return (
                      <tr key={String(row.provider_id)}>
                        <td className="mono">{String(row.provider_id)}</td>
                        <td>{String(row.model)}</td>
                        <td className="mono">{String(row.endpoint)}</td>
                        <td>
                          <Badge value={String(row.status)}>{String(row.status)}</Badge>
                        </td>
                        <td>
                          {String(row.provider_id) === defaultId ? (
                            <Badge className="badge-ok">默认</Badge>
                          ) : (
                            <button
                              className="btn btn-sm"
                              onClick={() => setDefault.mutate(row)}
                            >
                              设为默认
                            </button>
                          )}
                        </td>
                        <td>
                          <div className="btn-group">
                            <button
                              className="btn btn-sm"
                              onClick={() =>
                                setProviderForm({
                                  provider_id: String(row.provider_id),
                                  model: String(row.model),
                                  endpoint: String(row.endpoint),
                                  api_key_ref: String(config.api_key_ref ?? ""),
                                  backend: String(config.backend ?? "openai"),
                                  litellm_provider: String(
                                    config.litellm_provider ?? "",
                                  ),
                                  timeout_seconds: String(
                                    config.timeout_seconds ?? 5,
                                  ),
                                  thinking_mode: String(
                                    config.thinking_mode ?? "",
                                  ),
                                  reasoning_effort: String(
                                    config.reasoning_effort ?? "none",
                                  ),
                                  retries: String(config.retries ?? 5),
                                  streaming:
                                    config.streaming === true ? "true" : "false",
                                  max_tokens: String(config.max_tokens ?? ""),
                                  headers: JSON.stringify(
                                    config.headers ?? {},
                                    null,
                                    2,
                                  ),
                                })
                              }
                            >
                              编辑
                            </button>
                            <button
                              className="btn btn-sm"
                              onClick={() => probeProvider.mutate(row)}
                              disabled={probeProvider.isPending}
                            >
                              <Play className="" />
                              测试连接
                            </button>
                            <button
                              className="btn btn-sm btn-danger"
                              onClick={() => {
                                if (
                                  window.confirm(
                                    `确定删除供应商 ${String(row.provider_id)}？`,
                                  )
                                ) {
                                  deleteProvider.mutate(String(row.provider_id));
                                }
                              }}
                            >
                              <Trash2 className="" />
                              删除
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {Object.entries(probeResults).length > 0 ? (
            <div className="stack" style={{ gap: 8, marginTop: 12 }}>
              {Object.entries(probeResults).map(([providerId, result]) => (
                <div className="card" key={providerId}>
                  <div className="panel-head" style={{ marginBottom: 4 }}>
                    <div className="card-title" style={{ margin: 0 }}>
                      {providerId} 测试结果
                    </div>
                    <Badge value={String(result.status)}>{String(result.status)}</Badge>
                  </div>
                  {result.status === "ok" ? (
                    <pre className="result-box" style={{ maxHeight: 220 }}>
                      {JSON.stringify(result.capabilities, null, 2)}
                    </pre>
                  ) : (
                    <p className="muted" style={{ margin: 0 }}>
                      {String(result.detail ?? result.reason ?? "连接失败")}
                    </p>
                  )}
                </div>
              ))}
            </div>
          ) : null}
          {setDefault.isError ? (
            <Notice tone="error">{String(setDefault.error)}</Notice>
          ) : null}
          {deleteProvider.isError ? (
            <Notice tone="error">{String(deleteProvider.error)}</Notice>
          ) : null}
          {probeProvider.isError ? (
            <Notice tone="error">{String(probeProvider.error)}</Notice>
          ) : null}
        </Panel>
      ) : null}

      {tab === "runners" ? (
        <Panel title="执行单元" icon={Server}>
          <JsonImportForm
            title="批量导入执行单元"
            endpoint="/api/v1/runtime/runners/import"
            placeholder='[{"runner_id":"agent-worker","kind":"docker","status":"online"}]'
            onImported={() =>
              queryClient.invalidateQueries({ queryKey: ["runners"] })
            }
          />
          <RegistryForm
            fields={[
              { key: "runner_id", label: "Runner id", placeholder: "agent-worker" },
              { key: "kind", label: "类型", placeholder: "control-plane" },
            ]}
            values={runnerForm}
            onChange={(key, value) => setRunnerForm((current) => ({ ...current, [key]: value }))}
            onSubmit={() => registerRunner.mutate()}
            pending={registerRunner.isPending}
            submitLabel="登记执行单元"
          />
          {(runners.data as Array<Record<string, unknown>> | undefined)?.length ? (
            <RegistryTable
              rows={runners.data as Array<Record<string, unknown>>}
              columns={[
                { key: "runner_id", label: "Runner" },
                { key: "kind", label: "类型" },
                { key: "status", label: "状态" },
              ]}
            />
          ) : (
            <EmptyState
              title="暂无执行单元"
              description="运行 worker 后自动登记。"
              action={
                <button
                  className="btn btn-sm"
                  onClick={() => void runners.refetch()}
                >
                  刷新
                </button>
              }
            />
          )}
        </Panel>
      ) : null}

      {tab === "skills" ? (
        <Panel title="技能" icon={Boxes}>
          <Notice tone="info">
            技能以 SKILL.md 包为准；支持上传 .zip 技能包或直接粘贴 SKILL.md 内容。
          </Notice>
          <details className="registry-form">
            <summary>导入技能包</summary>
            <label className="field">
              SKILL.md / zip 包
              <input
                type="file"
                accept=".zip,.md,.markdown"
                onChange={(event) =>
                  setSkillPackageFile(event.target.files?.[0] ?? null)
                }
              />
            </label>
            <label className="field" style={{ marginTop: 8 }}>
              SKILL.md 内容（没有 zip 时使用）
              <textarea
                rows={8}
                value={skillMarkdown}
                onChange={(event) => setSkillMarkdown(event.target.value)}
                placeholder={
                  "---\nname: custom-recon\nversion: 1.0\ntrigger: web_discovery\n---\n\n# Custom Recon\n\n..."
                }
              />
            </label>
            <div className="btn-group" style={{ marginTop: 10 }}>
              <button
                className="btn btn-primary"
                onClick={() => importSkillPackage.mutate()}
                disabled={importSkillPackage.isPending}
              >
                <Upload className="" />
                {importSkillPackage.isPending ? "导入中..." : "导入技能包"}
              </button>
            </div>
            {skillImportResult ? (
              <div className="card" style={{ marginTop: 10 }}>
                <div className="panel-head" style={{ marginBottom: 4 }}>
                  <div className="card-title" style={{ margin: 0 }}>
                    {String(skillImportResult.name)} · v
                    {String(skillImportResult.version)}
                  </div>
                  <Badge value="imported">imported</Badge>
                </div>
                <p className="card-meta" style={{ margin: 0 }}>
                  {String(skillImportResult.package_path)} ·{" "}
                  {String(skillImportResult.files ?? []).length} files
                </p>
                {Array.isArray(skillImportResult.warnings) &&
                (skillImportResult.warnings as string[]).length ? (
                  <p className="muted" style={{ color: "#fbbf24" }}>
                    校验提示：{(skillImportResult.warnings as string[]).join("；")}
                  </p>
                ) : null}
              </div>
            ) : null}
            {skillImportError ? (
              <Notice tone="error">{skillImportError}</Notice>
            ) : null}
          </details>
          <RegistryForm
            fields={[
              { key: "skill_ref", label: "Skill ref", placeholder: "custom-recon" },
              { key: "name", label: "名称", placeholder: "Custom Recon" },
              { key: "version", label: "版本", placeholder: "1" },
              { key: "trigger", label: "触发", placeholder: '["recon"]' },
              { key: "runner", label: "Runner", placeholder: "docker" },
              { key: "risk_level", label: "风险", placeholder: "L1" },
              { key: "status", label: "状态", placeholder: "available" },
            ]}
            values={skillForm}
            onChange={(key, value) =>
              setSkillForm((current) => ({ ...current, [key]: value }))
            }
            onSubmit={() => registerSkill.mutate()}
            pending={registerSkill.isPending}
            submitLabel={skillForm.skill_ref ? "更新技能" : "注册技能"}
            summaryLabel="新增技能"
          />
          {skillRows.length ? (
            <div className="stack" style={{ gap: 8 }}>
              {skillRows.map((row) => (
                <div className="card" key={String(row.skill_ref)}>
                  <div className="panel-head" style={{ marginBottom: 4 }}>
                    <div className="card-title" style={{ margin: 0 }}>
                      {String(row.name)} <code>{String(row.skill_ref)}</code>
                    </div>
                    <Badge value={String(row.risk_level ?? "L1")}>
                      {String(row.risk_level ?? "L1")}
                    </Badge>
                  </div>
                  <p className="card-meta" style={{ margin: 0 }}>
                    v{String(row.version)} · 触发：
                    {Array.isArray(row.trigger)
                      ? String((row.trigger as string[]).join(", "))
                      : String(row.trigger ?? "-")}
                    {" "}· Runner：{String(row.required_runner ?? row.runner ?? "-")}
                  </p>
                  {row.description ? (
                    <p className="card-meta" style={{ margin: "4px 0 0" }}>
                      {String(row.description).slice(0, 160)}
                    </p>
                  ) : null}
                  <div className="btn-group" style={{ marginTop: 8 }}>
                    <button
                      className="btn btn-sm"
                      onClick={() => openSkill.mutate(String(row.skill_ref))}
                      disabled={openSkill.isPending}
                    >
                      查看定义
                    </button>
                    {String(row.source ?? "") !== "builtin" ? (
                      <>
                        <button
                          className="btn btn-sm"
                          onClick={() =>
                            setSkillForm({
                              skill_ref: String(row.skill_ref),
                              name: String(row.name ?? ""),
                              version: String(row.version ?? "1"),
                              trigger: Array.isArray(row.trigger)
                                ? JSON.stringify(row.trigger)
                                : String(row.trigger ?? ""),
                              runner: String(row.required_runner ?? row.runner ?? ""),
                              risk_level: String(row.risk_level ?? "L1"),
                              status: String(row.status ?? "available"),
                            })
                          }
                        >
                          编辑
                        </button>
                        <button
                          className="btn btn-sm btn-danger"
                          onClick={() => {
                            if (
                              window.confirm(
                                `确定删除技能 ${String(row.skill_ref)}？`,
                              )
                            ) {
                              deleteSkill.mutate(String(row.skill_ref));
                            }
                          }}
                        >
                          删除
                        </button>
                      </>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState
              title="暂无技能"
              description="技能由内置清单或 worker 装配。"
              action={
                <button
                  className="btn btn-sm"
                  onClick={() => void skills.refetch()}
                >
                  刷新
                </button>
              }
            />
          )}
          {openSkill.isSuccess && openSkill.data ? (
            <div
              ref={skillDetailRef}
              className="card"
              style={{ marginTop: 12 }}
            >
              <div className="panel-head" style={{ marginBottom: 4 }}>
                <div className="card-title" style={{ margin: 0 }}>
                  {String(openSkill.data.name)} · v{String(openSkill.data.version)}
                </div>
                <Badge value={String(openSkill.data.source ?? "custom")}>
                  {String(openSkill.data.source ?? "custom")}
                </Badge>
              </div>
              {openSkill.data.description ? (
                <p className="card-meta" style={{ margin: "4px 0 8px" }}>
                  {String(openSkill.data.description)}
                </p>
              ) : null}
              <div
                className="grid"
                style={{
                  gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                  gap: 6,
                  marginBottom: 8,
                }}
              >
                {[
                  ["分类", openSkill.data.category],
                  [
                    "触发",
                    Array.isArray(openSkill.data.trigger)
                      ? (openSkill.data.trigger as string[]).join(", ")
                      : openSkill.data.trigger,
                  ],
                  [
                    "工具",
                    Array.isArray(openSkill.data.required_tools)
                      ? (openSkill.data.required_tools as string[]).join(", ")
                      : openSkill.data.required_tools,
                  ],
                  ["Runner", openSkill.data.required_runner ?? openSkill.data.runner],
                  ["风险", openSkill.data.risk_level],
                  [
                    "CWE",
                    Array.isArray(openSkill.data.cwe_ids)
                      ? (openSkill.data.cwe_ids as string[]).join(", ")
                      : openSkill.data.cwe_ids,
                  ],
                  ["包路径", openSkill.data.package_path],
                  [
                    "资源",
                    Array.isArray(openSkill.data.files)
                      ? `${(openSkill.data.files as string[]).length} files`
                      : "0",
                  ],
                ].map(([label, value]) => (
                  <div key={String(label)} className="card-meta" style={{ margin: 0 }}>
                    <strong>{String(label)}</strong>
                    <div style={{ overflowWrap: "anywhere" }}>
                      {value == null || String(value) === ""
                        ? "-"
                        : String(value)}
                    </div>
                  </div>
                ))}
              </div>
              {Array.isArray(openSkill.data.files) &&
              (openSkill.data.files as string[]).length ? (
                <details style={{ marginBottom: 8 }}>
                  <summary>技能包资源</summary>
                  <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                    {(openSkill.data.files as string[]).map((file) => (
                      <li key={file}>
                        <code>{String(file)}</code>
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}
              <div style={{ maxHeight: 420, overflow: "auto" }}>
                <MarkdownView
                  value={
                    typeof openSkill.data.content === "string"
                      ? openSkill.data.content
                      : JSON.stringify(
                          openSkill.data.content ?? openSkill.data,
                          null,
                          2,
                        )
                  }
                />
              </div>
            </div>
          ) : null}
          {openSkill.isError ? (
            <Notice tone="error">{String(openSkill.error)}</Notice>
          ) : null}
          {registerSkill.isError ? (
            <Notice tone="error">{String(registerSkill.error)}</Notice>
          ) : null}
          {importSkillPackage.isError ? (
            <Notice tone="error">{String(importSkillPackage.error)}</Notice>
          ) : null}
        </Panel>
      ) : null}

      {tab === "mcp" ? (
        <Panel title="MCP 服务器" icon={PlugZap}>
          <Notice tone="info">
            常用 MCP 预设会自动填充命令；github 与 brave-search 需要对应环境变量。
          </Notice>
          <JsonImportForm
            title="批量导入 MCP 服务器"
            endpoint="/api/v1/runtime/mcp/import"
            placeholder='[{"server_id":"fetch","name":"Fetch","kind":"local","command":"npx --no-install mcp-fetch-server"}]'
            onImported={() =>
              queryClient.invalidateQueries({ queryKey: ["runtime-mcp"] })
            }
          />
          <label className="field" style={{ marginBottom: 10 }}>
            常用 MCP 预设
            <select
              value=""
              onChange={(event) => {
                const preset = mcpPresetRows.find(
                  (item) => item.id === event.target.value,
                );
                if (!preset) {
                  return;
                }
                setMcpForm({
                  server_id: String(preset.id),
                  name: String(preset.name),
                  kind: String(preset.kind),
                  command: String(preset.command),
                  description: String(preset.description ?? ""),
                  timeout_seconds: "10",
                  env: JSON.stringify(preset.env_hint ?? {}, null, 2),
                  status: "available",
                });
              }}
            >
              <option value="">选择 MCP 预设...</option>
              {mcpPresetRows.map((preset) => (
                <option key={String(preset.id)} value={String(preset.id)}>
                  {String(preset.name)} · {String(preset.description)}
                </option>
              ))}
            </select>
          </label>
          <RegistryForm
            fields={[
              { key: "server_id", label: "Server id", placeholder: "burp-mcp" },
              { key: "name", label: "名称", placeholder: "Burp MCP" },
              { key: "kind", label: "类型", placeholder: "local / sse / http" },
              { key: "command", label: "启动命令", placeholder: "npx -y @modelcontextprotocol/server" },
              { key: "description", label: "描述", placeholder: "MCP server description" },
              { key: "timeout_seconds", label: "超时（秒）", placeholder: "10" },
              { key: "env", label: "环境变量 JSON", placeholder: '{"API_KEY":"env:NAME"}' },
            ]}
            values={mcpForm}
            onChange={(key, value) => setMcpForm((current) => ({ ...current, [key]: value }))}
            onSubmit={() => registerMcp.mutate()}
            pending={registerMcp.isPending}
            submitLabel={mcpForm.server_id ? "更新 MCP" : "登记 MCP"}
            summaryLabel="新增/编辑 MCP"
          />
          {(mcp.data as unknown as Array<Record<string, unknown>> | undefined)?.length ? (
            <>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Server</th>
                      <th>名称</th>
                      <th>类型</th>
                      <th>启动命令 / URL</th>
                      <th>状态</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(mcp.data as unknown as Array<Record<string, unknown>>).map(
                      (row) => (
                        <tr key={String(row.server_id)}>
                          <td className="mono">{String(row.server_id)}</td>
                          <td>{String(row.name)}</td>
                          <td>{String(row.kind)}</td>
                          <td className="mono">{String(row.command)}</td>
                          <td>
                            <Badge value={String(row.status)}>{String(row.status)}</Badge>
                          </td>
                          <td>
                            <div className="btn-group">
                              <button
                                className="btn btn-sm"
                                onClick={() => testMcp.mutate(String(row.server_id))}
                                disabled={testMcp.isPending}
                              >
                                <Play className="" />
                                测试连接
                              </button>
                              <button
                                className="btn btn-sm"
                                onClick={() => {
                                  const config = (row.config as
                                    | Record<string, unknown>
                                    | undefined) ?? {};
                                  setMcpForm({
                                    server_id: String(row.server_id),
                                    name: String(row.name),
                                    kind: String(row.kind),
                                    command: String(row.command),
                                    description: String(
                                      config.description ?? "",
                                    ),
                                    timeout_seconds: String(
                                      config.timeout_seconds ?? 10,
                                    ),
                                    env: JSON.stringify(
                                      config.env ?? {},
                                      null,
                                      2,
                                    ),
                                    status: String(row.status),
                                  });
                                }}
                              >
                                编辑
                              </button>
                              <button
                                className="btn btn-sm btn-danger"
                                onClick={() => {
                                  if (
                                    window.confirm(
                                      `确定删除 MCP ${String(row.server_id)}？`,
                                    )
                                  ) {
                                    deleteMcp.mutate(String(row.server_id));
                                  }
                                }}
                              >
                                删除
                              </button>
                            </div>
                          </td>
                        </tr>
                      ),
                    )}
                  </tbody>
                </table>
              </div>
              {Object.entries(mcpTestResults).length > 0 ? (
                <div className="stack" style={{ gap: 8, marginTop: 12 }}>
                  {Object.entries(mcpTestResults).map(([serverId, result]) => (
                    <div className="card" key={serverId}>
                      <div className="panel-head" style={{ marginBottom: 2 }}>
                        <div className="card-title" style={{ margin: 0 }}>
                          {serverId}
                        </div>
                        <Badge value={String(result.status)}>
                          {String(result.status)}
                        </Badge>
                      </div>
                      <p className="card-meta" style={{ margin: 0 }}>
                        {String(result.detail ?? "")}
                      </p>
                      {result.suggestion ? (
                        <p
                          className="muted"
                          style={{ margin: "4px 0 0", color: "#fbbf24" }}
                        >
                          建议：{String(result.suggestion)}
                        </p>
                      ) : null}
                      {(
                        (result.tools as Array<Record<string, unknown>> | undefined) ??
                        []
                      ).length ? (
                        <details style={{ marginTop: 6 }}>
                          <summary>
                            {String(
                              (result.tools as Array<Record<string, unknown>>)
                                .length,
                            )}{" "}
                            tools
                          </summary>
                          <div className="table-wrap" style={{ marginTop: 6 }}>
                            <table className="data-table">
                              <thead>
                                <tr>
                                  <th>Tool</th>
                                  <th>说明</th>
                                </tr>
                              </thead>
                              <tbody>
                                {(
                                  result.tools as Array<
                                    Record<string, unknown>
                                  >
                                ).map((tool) => (
                                  <tr key={String(tool.name)}>
                                    <td className="mono">
                                      {String(tool.name)}
                                    </td>
                                    <td className="muted">
                                      {String(tool.description ?? "")}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </details>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : null}
            </>
          ) : (
            <EmptyState
              title="暂无 MCP"
              description="登记 MCP 服务器后供 Agent 发现工具。"
              action={
                <button
                  className="btn btn-sm"
                  onClick={() =>
                    document
                      .querySelector(".registry-form")
                      ?.scrollIntoView({ behavior: "smooth" })
                  }
                >
                  登记 MCP
                </button>
              }
            />
          )}
        </Panel>
      ) : null}

      {tab === "tools" ? (
        <Panel title="安全工具集成" icon={Wrench}>
          <Notice tone="info">
            以下为系统集成的安全工具包与可用性；工具按能力分组，缺少镜像的包会在运行时自动降级。
          </Notice>
          <div className="toolbar">
            <input
              type="text"
              aria-label="搜索工具目录"
              placeholder={`搜索 ${allTools.length} 个工具（ref / 能力 / 包名）`}
              value={toolSearch}
              onChange={(event) => setToolSearch(event.target.value)}
            />
            <span className="muted" style={{ fontSize: 12 }}>
              {filteredTools.length} / {allTools.length} 个工具
            </span>
          </div>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>工具</th>
                  <th>包</th>
                  <th>能力</th>
                  <th>风险</th>
                  <th>运行方式</th>
                  <th>说明</th>
                </tr>
              </thead>
              <tbody>
                {filteredTools.map((tool) => (
                  <tr key={String(tool.ref)}>
                    <td className="mono">
                      {String(tool.ref)}{" "}
                      {SMOKE_VERIFIED_TOOLS.has(String(tool.ref)) ? (
                        <Badge value="已验证">已验证</Badge>
                      ) : null}
                    </td>
                    <td>
                      <Badge value={String(tool.pack)}>{String(tool.pack)}</Badge>
                    </td>
                    <td>{String(tool.capability)}</td>
                    <td>{String(tool.risk_level)}</td>
                    <td>{String(tool.runner)}</td>
                    <td className="muted">{String(tool.description)}</td>
                  </tr>
                ))}
                {filteredTools.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="muted">
                      没有匹配的工具。
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
          {toolPackRows.length ? (
            <div className="stack" style={{ gap: 8 }}>
              {toolPackRows.map((pack) => {
                const availability = pack.availability as
                  | Record<string, unknown>
                  | undefined;
                const tools = (pack.tools as Array<Record<string, unknown>> | undefined) ?? [];
                return (
                  <details className="card" key={String(pack.name)} style={{ padding: 10 }}>
                    <summary style={{ cursor: "pointer" }}>
                      <div className="panel-head" style={{ marginBottom: 2 }}>
                        <div className="card-title" style={{ margin: 0 }}>
                          {String(pack.name)} <code>v{String(pack.version)}</code>
                        </div>
                        <Badge value={String(availability?.status ?? "unknown")}>
                          {String(availability?.status ?? "unknown")}
                        </Badge>
                      </div>
                      <p className="card-meta" style={{ margin: 0 }}>
                        镜像：{String(pack.image || "本地/浏览器")} · 能力：{String((pack.capabilities as unknown[])?.length ?? 0)} · 工具：{tools.length}
                      </p>
                    </summary>
                    <div className="table-wrap" style={{ marginTop: 8 }}>
                      <table className="data-table">
                        <thead>
                          <tr>
                            <th>工具</th>
                            <th>能力</th>
                            <th>风险</th>
                            <th>运行方式</th>
                            <th>说明</th>
                          </tr>
                        </thead>
                        <tbody>
                          {tools.map((tool) => (
                            <tr key={String(tool.ref)}>
                              <td className="mono">{String(tool.ref)}</td>
                              <td>{String(tool.capability)}</td>
                              <td>{String(tool.risk_level)}</td>
                              <td>{String(tool.runner)}</td>
                              <td className="muted">{String(tool.description)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </details>
                );
              })}
            </div>
          ) : (
            <EmptyState
              title="暂无工具包"
              description="由 deploy/toolpacks 装配。"
              action={
                <button
                  className="btn btn-sm"
                  onClick={() => void toolPacks.refetch()}
                >
                  刷新
                </button>
              }
            />
          )}
        </Panel>
      ) : null}

      {tab === "audit" ? (
        <Panel title="审计日志" icon={ShieldCheck}>
          <Notice tone="info">
            记录关键控制面操作：项目、运行、供应商、资产、漏洞与审批。
          </Notice>
          {auditRows.length ? (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>操作者</th>
                    <th>动作</th>
                    <th>资源</th>
                    <th>详情</th>
                    <th>IP</th>
                  </tr>
                </thead>
                <tbody>
                  {auditRows.map((row, index) => (
                    <tr key={index}>
                      <td className="mono">{String(row.created_at ?? "")}</td>
                      <td>{String(row.actor ?? "-")}</td>
                      <td className="mono">{String(row.action ?? "-")}</td>
                      <td className="mono">{String(row.resource ?? "-")}</td>
                      <td className="muted">{String(row.detail ?? "")}</td>
                      <td className="mono">{String(row.ip ?? "")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState
              title="暂无审计记录"
              description="控制面操作会记录在这里。"
              action={
                <button
                  className="btn btn-sm"
                  onClick={() => void auditLogs.refetch()}
                >
                  刷新
                </button>
              }
            />
          )}
        </Panel>
      ) : null}

      {tab === "retrieval" ? (
        <Panel title="检索与存储" icon={Database}>
          <Notice tone="info">
            集中配置嵌入模型、向量库、知识图谱与重排；任务未单独指定检索配置时使用这里的默认值。
          </Notice>
          <div className="form-grid">
            <label className="field">
              嵌入后端
              <select
                value={retrievalForm.embedding_backend}
                onChange={(event) =>
                  setRetrievalForm((current) => ({
                    ...current,
                    embedding_backend: event.target.value,
                  }))
                }
              >
                <option value="openai_compatible">openai-compatible</option>
                <option value="ollama">ollama</option>
                <option value="local">local（本地模型）</option>
                <option value="none">none</option>
              </select>
            </label>
            <label className="field">
              嵌入模型
              <input
                value={retrievalForm.embedding_model}
                onChange={(event) =>
                  setRetrievalForm((current) => ({
                    ...current,
                    embedding_model: event.target.value,
                  }))
                }
                placeholder="nomic-embed-text"
              />
            </label>
            <label className="field">
              嵌入端点
              <input
                value={retrievalForm.embedding_endpoint}
                onChange={(event) =>
                  setRetrievalForm((current) => ({
                    ...current,
                    embedding_endpoint: event.target.value,
                  }))
                }
                placeholder="http://127.0.0.1:11434/v1"
              />
            </label>
            <label className="field">
              API Key 引用
              <input
                value={retrievalForm.embedding_api_key_ref}
                onChange={(event) =>
                  setRetrievalForm((current) => ({
                    ...current,
                    embedding_api_key_ref: event.target.value,
                  }))
                }
                placeholder="env:OPENAI_API_KEY"
              />
            </label>
            <label className="field">
              向量库
              <select
                value={retrievalForm.vector_store}
                onChange={(event) =>
                  setRetrievalForm((current) => ({
                    ...current,
                    vector_store: event.target.value,
                  }))
                }
              >
                <option value="pgvector">pgvector</option>
                <option value="qdrant">qdrant</option>
                <option value="chroma">chroma</option>
                <option value="sqlite">sqlite（本地默认）</option>
              </select>
            </label>
            <label className="field">
              向量库地址 / 连接串
              <input
                value={
                  retrievalForm.vector_store === "pgvector"
                    ? retrievalForm.vector_database_url
                    : retrievalForm.vector_url
                }
                onChange={(event) =>
                  setRetrievalForm((current) => ({
                    ...current,
                    vector_database_url: event.target.value,
                    vector_url: event.target.value,
                  }))
                }
                placeholder="postgresql://veridix:veridix@127.0.0.1:55432/veridix"
              />
            </label>
            <label className="field">
              Collection
              <input
                value={retrievalForm.vector_collection}
                onChange={(event) =>
                  setRetrievalForm((current) => ({
                    ...current,
                    vector_collection: event.target.value,
                  }))
                }
                placeholder="veridix_chunks"
              />
            </label>
            <label className="field">
              知识图谱
              <select
                value={retrievalForm.graph_backend}
                onChange={(event) =>
                  setRetrievalForm((current) => ({
                    ...current,
                    graph_backend: event.target.value,
                  }))
                }
              >
                <option value="neo4j">neo4j</option>
                <option value="sqlite">sqlite（本地默认）</option>
              </select>
            </label>
            <label className="field">
              图谱地址
              <input
                value={retrievalForm.graph_uri}
                onChange={(event) =>
                  setRetrievalForm((current) => ({
                    ...current,
                    graph_uri: event.target.value,
                  }))
                }
                placeholder="bolt://127.0.0.1:7687"
              />
            </label>
            <label className="field">
              图谱用户名
              <input
                value={retrievalForm.graph_user}
                onChange={(event) =>
                  setRetrievalForm((current) => ({
                    ...current,
                    graph_user: event.target.value,
                  }))
                }
                placeholder="neo4j"
              />
            </label>
            <label className="field">
              图谱密码
              <input
                type="password"
                value={retrievalForm.graph_password}
                onChange={(event) =>
                  setRetrievalForm((current) => ({
                    ...current,
                    graph_password: event.target.value,
                  }))
                }
                placeholder="neo4j 密码"
              />
            </label>
            <label className="field">
              重排后端
              <select
                value={retrievalForm.rerank_backend}
                onChange={(event) =>
                  setRetrievalForm((current) => ({
                    ...current,
                    rerank_backend: event.target.value,
                  }))
                }
              >
                <option value="fastembed">fastembed（本地 ONNX）</option>
                <option value="openai_compatible">openai-compatible</option>
              </select>
            </label>
            <label className="field">
              重排模型
              <input
                value={retrievalForm.rerank_model}
                onChange={(event) =>
                  setRetrievalForm((current) => ({
                    ...current,
                    rerank_model: event.target.value,
                  }))
                }
                placeholder="BAAI/bge-reranker-base"
              />
            </label>
            <label className="field">
              融合策略
              <select
                value={retrievalForm.fusion}
                onChange={(event) =>
                  setRetrievalForm((current) => ({
                    ...current,
                    fusion: event.target.value,
                  }))
                }
              >
                <option value="rrf">RRF（默认）</option>
                <option value="weighted">加权线性融合</option>
                <option value="vector_first">向量优先</option>
              </select>
            </label>
            <label className="field">
              检索超时（秒）
              <input
                type="number"
                min={1}
                max={60}
                value={retrievalForm.deadline_seconds}
                onChange={(event) =>
                  setRetrievalForm((current) => ({
                    ...current,
                    deadline_seconds: event.target.value,
                  }))
                }
              />
            </label>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={retrievalForm.rerank_enabled === "true"}
                onChange={(event) =>
                  setRetrievalForm((current) => ({
                    ...current,
                    rerank_enabled: event.target.checked ? "true" : "false",
                  }))
                }
              />
              启用重排
            </label>
          </div>
          <div className="btn-group" style={{ marginTop: 10 }}>
            <button
              className="btn"
              onClick={() => testRetrieval.mutate()}
              disabled={testRetrieval.isPending}
            >
              <Play className="" />
              {testRetrieval.isPending ? "测试中..." : "测试检索连接"}
            </button>
            <button
              className="btn"
              onClick={applyMatureRetrievalDefaults}
            >
              填入成熟默认
            </button>
            <button
              className="btn btn-primary"
              onClick={() => saveRetrieval.mutate()}
              disabled={saveRetrieval.isPending}
            >
              <Save className="" />
              保存检索配置
            </button>
          </div>
          {Object.keys(retrievalProbe).length > 0 ? (
            <div className="card-grid" style={{ marginTop: 14 }}>
              {Object.entries(retrievalProbe).map(([key, value]) => (
                <div className="card" key={key}>
                  <div className="panel-head" style={{ marginBottom: 4 }}>
                    <div className="card-title" style={{ margin: 0 }}>
                      <code>{key}</code>
                    </div>
                    <Badge
                      value={String(
                        (value as Record<string, unknown>).status ?? "unknown",
                      )}
                    >
                      {String(
                        (value as Record<string, unknown>).status ?? "unknown",
                      )}
                    </Badge>
                  </div>
                  <p className="muted" style={{ fontSize: 12 }}>
                    {String((value as Record<string, unknown>).detail ?? "")}
                  </p>
                </div>
              ))}
            </div>
          ) : null}
          <div className="card-grid" style={{ marginTop: 14 }}>
            {storageBackendEntries.map(([key, value]) => (
              <div className="card" key={key}>
                <div className="panel-head" style={{ marginBottom: 4 }}>
                  <div className="card-title" style={{ margin: 0 }}>
                    <code>{key}</code>
                  </div>
                  <Badge
                    value={String(
                      (value as Record<string, string>).status ?? "ok",
                    )}
                  >
                    {String(
                      (value as Record<string, string>).status ?? "ok",
                    )}
                  </Badge>
                </div>
                <pre
                  className="result-box"
                  style={{ maxHeight: 130, fontSize: 11 }}
                >
                  {JSON.stringify(value, null, 2)}
                </pre>
              </div>
            ))}
          </div>
        </Panel>
      ) : null}

      {tab === "templates" ? (
        <Panel
          title="角色模板"
          icon={ListTree}
          actions={
            <span className="muted" style={{ fontSize: 12 }}>
              {templateRows.length} 个
            </span>
          }
        >
          <Notice tone="info">
            内置模板由 Agent 运行时提供；自定义模板保存后可作为任务的 role_template 使用。
          </Notice>
          <JsonImportForm
            title="批量导入角色模板"
            endpoint="/api/v1/runtime/role-templates/import"
            placeholder='[{"template_id":"custom_recon","label":"Custom Recon","description":"...","roles":[{"role_id":"recon"}]}]'
            onImported={() =>
              queryClient.invalidateQueries({ queryKey: ["role-templates"] })
            }
          />
          <RegistryForm
            fields={[
              { key: "template_id", label: "Template id", placeholder: "custom_recon" },
              { key: "label", label: "名称", placeholder: "Custom Recon" },
              { key: "description", label: "描述", placeholder: "自定义侦察模板" },
              { key: "roles", label: "Roles (JSON 数组)", placeholder: '[{"role_id":"discovery",...}]' },
            ]}
            values={templateForm}
            onChange={(key, value) =>
              setTemplateForm((current) => ({ ...current, [key]: value }))
            }
            onSubmit={() => saveTemplate.mutate()}
            pending={saveTemplate.isPending}
            submitLabel="保存角色模板"
            summaryLabel="新增/保存角色模板"
          />
          {templateRows.length === 0 ? (
            <EmptyState
              title="暂无角色模板"
              description="运行时内置模板会在此展示。"
              action={
                <button
                  className="btn btn-sm"
                  onClick={() => void roleTemplates.refetch()}
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
                    <th>Template</th>
                    <th>名称</th>
                    <th>描述</th>
                    <th>角色</th>
                    <th>类型</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {templateRows.map((template) => (
                    <tr key={String(template.template_id)}>
                      <td className="mono">{String(template.template_id)}</td>
                      <td>{String(template.label ?? "")}</td>
                      <td className="muted">{String(template.description ?? "")}</td>
                      <td>
                        {Array.isArray(template.roles)
                          ? (template.roles as unknown[])
                              .map((role) => roleLabel(role))
                              .join(", ")
                          : "-"}
                      </td>
                      <td>
                        <Badge value={template.builtin ? "builtin" : "custom"}>
                          {template.builtin ? "内置" : "自定义"}
                        </Badge>
                      </td>
                      <td>
                        <div className="btn-group">
                          <button
                            className="btn btn-sm"
                            onClick={() => setTemplateDetail(template)}
                          >
                            查看
                          </button>
                          {!template.builtin ? (
                            <>
                              <button
                                className="btn btn-sm"
                                onClick={() => {
                                  setTemplateForm({
                                    template_id: String(template.template_id),
                                    label: String(template.label ?? ""),
                                    description: String(template.description ?? ""),
                                    roles: JSON.stringify(
                                      template.roles ?? [],
                                      null,
                                      2,
                                    ),
                                  });
                                  setTemplateDetail(template);
                                }}
                              >
                                编辑
                              </button>
                              <button
                                className="btn btn-sm btn-danger"
                                onClick={() => {
                                  if (
                                    window.confirm(
                                      `确定删除角色模板 ${String(template.template_id)}？`,
                                    )
                                  ) {
                                    deleteTemplate.mutate(String(template.template_id));
                                  }
                                }}
                              >
                                删除
                              </button>
                            </>
                          ) : (
                            <button
                              className="btn btn-sm"
                              onClick={() => {
                                setTemplateForm({
                                  template_id: `${String(template.template_id)}_copy`,
                                  label: `${String(template.label ?? "")} Copy`,
                                  description: String(template.description ?? ""),
                                  roles: JSON.stringify(
                                    template.roles ?? [],
                                    null,
                                    2,
                                  ),
                                });
                                setTemplateDetail(null);
                              }}
                            >
                              复制
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {templateDetail ? (
            <div className="card" style={{ marginTop: 12 }}>
              <div className="panel-head" style={{ marginBottom: 4 }}>
                <div className="card-title" style={{ margin: 0 }}>
                  {String(templateDetail.label ?? templateDetail.template_id)}
                </div>
                <Badge value={templateDetail.builtin ? "builtin" : "custom"}>
                  {templateDetail.builtin ? "内置" : "自定义"}
                </Badge>
              </div>
              <div style={{ margin: "4px 0 8px" }}>
                <MarkdownView value={String(templateDetail.description ?? "")} />
              </div>
              <div
                className="grid"
                style={{
                  gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                  gap: 6,
                  marginBottom: 8,
                }}
              >
                {(Array.isArray(templateDetail.roles)
                  ? (templateDetail.roles as unknown[])
                  : []
                ).map((role, index) => {
                  const detail =
                    typeof role === "string"
                      ? { role_id: role, name: role }
                      : (role as Record<string, unknown>);
                  return (
                    <div key={index} className="card-meta" style={{ margin: 0 }}>
                      <strong>{roleLabel(role)}</strong>
                      <pre className="result-box" style={{ maxHeight: 160 }}>
                        {JSON.stringify(detail, null, 2)}
                      </pre>
                    </div>
                  );
                })}
              </div>
              <button
                className="btn btn-sm"
                onClick={() => setTemplateDetail(null)}
              >
                关闭详情
              </button>
            </div>
          ) : null}
          {saveTemplate.isError ? (
            <Notice tone="error">{String(saveTemplate.error)}</Notice>
          ) : null}
          {deleteTemplate.isError ? (
            <Notice tone="error">{String(deleteTemplate.error)}</Notice>
          ) : null}
        </Panel>
      ) : null}

      {tab === "loops" ? (
        <Panel
          title="Loop Profiles"
          icon={GitBranch}
          actions={
            <span className="muted" style={{ fontSize: 12 }}>
              {loopProfileRows.length} 个
            </span>
          }
        >
          <Notice tone="info">
            Loop Profile 是逐节点声明式契约：知识查询、技能白名单、Oracle、
            证据要求和 Sandbox 策略会随角色节点实际进入模型上下文。
          </Notice>
          {loopProfileRows.length === 0 ? (
            <EmptyState
              title="暂无 Loop Profiles"
              description="控制面未返回声明式 Loop 契约。"
              action={
                <button
                  className="btn btn-sm"
                  onClick={() => void loopProfiles.refetch()}
                >
                  刷新
                </button>
              }
            />
          ) : (
            <RegistryTable
              rows={loopProfileRows}
              columns={[
                { key: "name", label: "Name" },
                { key: "version", label: "Version" },
                { key: "category", label: "Category" },
                { key: "oracle", label: "Oracle" },
                { key: "success", label: "Success Criteria" },
                { key: "risk", label: "Risk" },
                { key: "sandbox", label: "Sandbox" },
                { key: "knowledge", label: "Knowledge Query" },
                { key: "evidence", label: "Evidence" },
              ]}
            />
          )}
        </Panel>
      ) : null}

      {diagnostics.isLoading && <Loading label="加载诊断" />}
      {diagnostics.isError && <ErrorBanner message={String(diagnostics.error)} />}
      <p className="muted" style={{ fontSize: 11 }}>
        控制面 {CONTROL_URL}
      </p>
    </section>
  );
}
