import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CirclePlay,
  Database,
  ExternalLink,
  MessageSquareText,
  Rocket,
  Settings2,
  Target,
} from "lucide-react";
import { control } from "../api.js";
import { ErrorBanner } from "../components/Status.js";
import { Notice, Panel } from "../components/ui.js";
import { useNavigate } from "react-router-dom";
import { useRunSelection } from "../store.js";

const STEPS = [
  { id: 0, label: "目标与项目", icon: Target },
  { id: 1, label: "任务意图", icon: MessageSquareText },
  { id: 2, label: "执行策略", icon: Settings2 },
  { id: 3, label: "确认创建", icon: Rocket },
];

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

function isValidTarget(value: string, template: string): boolean {
  const text = value.trim();
  if (!text) {
    return false;
  }
  if (template === "code-audit") {
    return true;
  }
  if (
    text.startsWith("/workspace/") ||
    text.startsWith("workspace://") ||
    text.startsWith("file://")
  ) {
    return true;
  }
  return isValidUrl(text);
}

function FieldError({ message }: { message?: string }) {
  if (!message) {
    return null;
  }
  return (
    <span className="field-error">
      <AlertCircle className="" />
      {message}
    </span>
  );
}

export function MissionSetup() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const setSelectedRunId = useRunSelection((state) => state.setSelectedRunId);
  const [step, setStep] = useState(0);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [projectName, setProjectName] = useState("my-lab");
  const [missionName, setMissionName] = useState("web discovery");
  const [targetUrl, setTargetUrl] = useState("https://lab.example.test");
  const [missionIntent, setMissionIntent] = useState(
    "对目标执行 Web 探测，识别并验证常见 Web 漏洞（如 SQLi / XSS / SSRF），整理可复现证据。",
  );
  const [providerEndpoint, setProviderEndpoint] = useState("");
  const [providerModel, setProviderModel] = useState("");
  const [apiKeyRef, setApiKeyRef] = useState("");
  const [maxTurns, setMaxTurns] = useState("5");
  const [retryTransient, setRetryTransient] = useState("3");
  const [wallClockSeconds, setWallClockSeconds] = useState("0");
  const [streaming, setStreaming] = useState(false);
  const [reasoningEffort, setReasoningEffort] = useState("none");
  const [providerRetries, setProviderRetries] = useState("5");
  const [budgetPolicy, setBudgetPolicy] = useState("continue");
  const [parallelToolCalls, setParallelToolCalls] = useState(false);
  const [providerModelOptions, setProviderModelOptions] = useState<string[]>([]);
  const [executionWaitSeconds, setExecutionWaitSeconds] = useState("30");
  const [executionPollInterval, setExecutionPollInterval] = useState("2");
  const [executionStrict, setExecutionStrict] = useState(false);
  const [executionCommand, setExecutionCommand] = useState("");
  const [template, setTemplate] = useState("default");
  const [providerChoice, setProviderChoice] = useState("default");
  const [executionNode, setExecutionNode] = useState("local");
  const remoteNodes = useQuery({
    queryKey: ["remote-nodes"],
    queryFn: () => control.requestPublic("/api/v1/remote/nodes"),
  });
  const loopPresetsQuery = useQuery({
    queryKey: ["loop-presets"],
    queryFn: () => control.listLoopPresets(),
  });
  const loopPresetRows = Object.values(
    (loopPresetsQuery.data ?? {}) as Record<string, Record<string, unknown>>,
  );
  const [requiredCategories, setRequiredCategories] = useState("XSS,SQLi");
  const [minSeverity, setMinSeverity] = useState("high");
  const [requireEvidence, setRequireEvidence] = useState(true);
  const [dedupe, setDedupe] = useState(true);
  const [blockConflicts, setBlockConflicts] = useState(true);
  const [scannerTools, setScannerTools] = useState("web.nikto.scan");
  const [toolArgs, setToolArgs] = useState("{}");
  const [forcedToolArgs, setForcedToolArgs] = useState("{}");
  const [loopProfiles, setLoopProfiles] = useState("{}");
  const [loopPreset, setLoopPreset] = useState("");
  const [retrievalLevel, setRetrievalLevel] = useState("lexical");
  const [memoryTokenBudget, setMemoryTokenBudget] = useState("2000");
  const [memoryLimit, setMemoryLimit] = useState("20");
  const [memoryRetrievalLevel, setMemoryRetrievalLevel] = useState("hybrid");
  const [created, setCreated] = useState<Record<string, string>>({});
  const [missionId, setMissionId] = useState<string | null>(null);
  const providersQuery = useQuery({
    queryKey: ["providers"],
    queryFn: () => control.listProviders(),
  });
  const providerRows = (providersQuery.data ?? []) as Array<
    Record<string, unknown>
  >;
  const selectedProvider = providerRows.find(
    (row) => row.provider_id === providerChoice,
  );
  const selectedConfig = (selectedProvider?.config ?? {}) as Record<
    string,
    unknown
  >;
  const fetchModels = useMutation({
    mutationFn: async () => {
      const result = await control.requestJson<{ models?: string[] }>(
        "POST",
        "/api/v1/providers/models",
        {
          provider_id:
            providerChoice === "custom" ? "custom" : providerChoice,
          endpoint: providerEndpoint.trim(),
          model: providerModel.trim() || "probe",
          api_key_ref: apiKeyRef.trim() || undefined,
          timeout_seconds: 8,
        },
      );
      const models = (result.models as string[] | undefined) ?? [];
      setProviderModelOptions(models);
      if (models.length > 0 && !models.includes(providerModel.trim())) {
        setProviderModel(models[0]);
      }
      return models;
    },
  });
  const create = useMutation({
    mutationFn: async () => {
      const project = await control.createProject(projectName);
      const target = await control.createTarget(project.project_id, targetUrl);
      const spec: Record<string, unknown> = {
        target_id: target.target_id,
        mission: missionIntent,
        max_turns: Number(maxTurns) || 5,
        budget: {
          retry_transient: Number(retryTransient) || 0,
          ...(parallelToolCalls ? { parallel_tool_calls: true } : {}),
          ...(Number(wallClockSeconds) > 0
            ? { wall_clock_seconds: Number(wallClockSeconds) }
            : {}),
        },
        streaming,
        budget_policy: budgetPolicy,
        retrieval: {
          level: retrievalLevel,
        },
        memory: {
          token_budget: Number(memoryTokenBudget) || 2000,
          limit: Number(memoryLimit) || 20,
          retrieval_level: memoryRetrievalLevel,
        },
      };
      try {
        spec.loop_profiles = JSON.parse(loopProfiles || "{}");
      } catch {
        throw new Error("Loop Profiles 覆盖必须是 JSON 对象");
      }
      if (
        template === "scanner-verify" ||
        template === "toolchain-verify" ||
        template === "code-audit" ||
        template === "redteam-orchestration"
      ) {
        spec.mode = "multi_role";
        spec.role_template =
          template === "code-audit"
            ? "code_audit"
            : template === "redteam-orchestration"
              ? "redteam_orchestration"
              : "scanner_verify";
        spec.required_categories =
          template === "code-audit"
            ? ["security", "HardcodedSecret"]
            : requiredCategories
                .split(",")
                .map((item) => item.trim())
                .filter(Boolean);
        spec.min_severity = template === "code-audit" ? "low" : minSeverity;
        spec.require_evidence = requireEvidence;
        spec.dedupe = dedupe;
        spec.conflict_blocks = blockConflicts;
        if (template === "code-audit") {
          spec.code_tools = ["code.sast.semgrep", "code.secrets.detect"];
          spec.scanner_tools = ["code.sast.semgrep", "code.secrets.detect"];
          spec.allowed_tools = [
            "code.sast.semgrep",
            "code.secrets.detect",
            "run.finish",
          ];
        } else {
          spec.scanner_tools = scannerTools
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean);
          spec.allowed_tools = [
            ...(spec.scanner_tools as string[]),
            "run.finish",
          ];
        }
        try {
          spec.tool_args = JSON.parse(toolArgs || "{}");
        } catch {
          spec.tool_args = {};
        }
        try {
          spec.forced_tool_args = JSON.parse(forcedToolArgs || "{}");
        } catch {
          spec.forced_tool_args = {};
        }
      }
      if (executionNode && executionNode !== "local") {
        spec.execution = {
          node_id: executionNode,
          wait_seconds: Number(executionWaitSeconds) || 30,
          poll_interval: Number(executionPollInterval) || 2,
          strict: executionStrict,
          ...(executionCommand.trim()
            ? { command: executionCommand.trim() }
            : {}),
        };
      }
      let endpoint = providerEndpoint.trim();
      let model = providerModel.trim();
      const providerOverrides: Record<string, unknown> = {
        retries: Number(providerRetries) || 5,
        reasoning_effort:
          reasoningEffort === "none" ? undefined : reasoningEffort,
        streaming,
      };
      if (providerChoice === "custom") {
        if (!endpoint || !model) {
          throw new Error("Provider endpoint 与 model 必须同时填写");
        }
        spec.provider = {
          endpoint,
          model,
          api_key_ref: apiKeyRef.trim() || undefined,
          config: providerOverrides,
        };
      } else if (providerChoice !== "default") {
        endpoint = String(selectedProvider?.endpoint ?? "");
        model = String(selectedProvider?.model ?? "");
        spec.provider = {
          endpoint,
          model,
          api_key_ref:
            String(selectedConfig.api_key_ref ?? "") || undefined,
          config: { ...selectedConfig, ...providerOverrides },
        };
      }
      const mission = await control.createMission(
        project.project_id,
        missionName,
        spec,
      );
      return { project, target, mission };
    },
    onSuccess: (result) => {
      setCreated({
        project: result.project.project_id,
        target: result.target.target_id,
        mission: result.mission.mission_id,
      });
      setMissionId(result.mission.mission_id);
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
  });
  const startRun = useMutation({
    mutationFn: () => control.startRun(missionId!, crypto.randomUUID()),
    onSuccess: (run) => {
      setCreated((current) => ({ ...current, run: run.run_id }));
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
  });

  const validateStep = (target: number): boolean => {
    const nextErrors: Record<string, string> = {};
    if (target === 0) {
      if (!projectName.trim()) {
        nextErrors.projectName = "项目名称不能为空";
      }
      if (!missionName.trim()) {
        nextErrors.missionName = "任务名称不能为空";
      }
      if (!isValidTarget(targetUrl, template)) {
        nextErrors.targetUrl =
          template === "code-audit"
            ? "请输入代码审计目标路径，例如 /workspace/input 或 file:///..."
            : "请输入 http:// 或 https:// 开头的目标 URL";
      }
    }
    if (target === 1 && missionIntent.trim().length < 8) {
      nextErrors.missionIntent = "任务意图至少 8 个字符，说明目标与期望动作";
    }
    if (target === 2) {
      const turns = Number(maxTurns);
      if (!Number.isFinite(turns) || turns < 1) {
        nextErrors.maxTurns = "最大轮次必须 >= 1";
      }
      if (toolArgs.trim() && !toolArgs.trim().startsWith("{")) {
        nextErrors.toolArgs = "Tool args 必须是 JSON 对象，例如 {}";
      }
      if (
        forcedToolArgs.trim() &&
        !forcedToolArgs.trim().startsWith("{")
      ) {
        nextErrors.forcedToolArgs =
          "Forced tool args 必须是 JSON 对象，例如 {}";
      }
      if (loopProfiles.trim() && !loopProfiles.trim().startsWith("{")) {
        nextErrors.loopProfiles =
          "Loop Profiles 覆盖必须是 JSON 对象，例如 {\"discovery\":{\"knowledge_query\":[\"custom\"]}}";
      }
      const memoryBudget = Number(memoryTokenBudget);
      if (!Number.isFinite(memoryBudget) || memoryBudget < 0) {
        nextErrors.memoryTokenBudget = "记忆 token 预算必须 >= 0";
      }
      const memoryLimitNumber = Number(memoryLimit);
      if (!Number.isFinite(memoryLimitNumber) || memoryLimitNumber < 1) {
        nextErrors.memoryLimit = "记忆最大条数必须 >= 1";
      }
    }
    setErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  };

  const next = () => {
    if (validateStep(step)) {
      setStep((current) => Math.min(current + 1, STEPS.length - 1));
    }
  };
  const back = () => {
    setErrors({});
    setStep((current) => Math.max(current - 1, 0));
  };

  return (
    <section>
      <header className="page-head">
        <div className="page-head-copy">
          <p className="page-eyebrow">Mission Setup</p>
          <h1>新建任务</h1>
          <p className="page-sub">
            分步配置目标、任务意图与执行策略，创建后即可启动 Agent 运行。
          </p>
        </div>
      </header>
      {create.isError && <ErrorBanner message={String(create.error)} />}
      <div className="wizard-steps">
        {STEPS.map((item) => {
          const state =
            item.id < step ? "done" : item.id === step ? "active" : "pending";
          return (
            <button
              key={item.id}
              className={`wizard-step-btn ${state}`}
              onClick={() => {
                if (item.id < step || validateStep(step)) {
                  setStep(item.id);
                }
              }}
              disabled={item.id > step}
              title={
                item.id > step
                  ? "请先完成当前步骤"
                  : undefined
              }
            >
              <span className="wizard-step-icon">
                {state === "done" ? (
                  <Check className="" />
                ) : (
                  <item.icon className="" />
                )}
              </span>
              <span className="wizard-step-label">{item.label}</span>
            </button>
          );
        })}
      </div>
      <div className="split">
        <div className="stack">
          {step === 0 ? (
            <Panel title="目标与项目" icon={Target}>
              <div className="form-grid">
                <label className="field">
                  项目名称
                  <input
                    value={projectName}
                    onChange={(event) => setProjectName(event.target.value)}
                    placeholder="my-lab"
                  />
                  <FieldError message={errors.projectName} />
                </label>
                <label className="field">
                  任务名称
                  <input
                    value={missionName}
                    onChange={(event) => setMissionName(event.target.value)}
                    placeholder="web discovery"
                  />
                  <FieldError message={errors.missionName} />
                </label>
                <label className="field" style={{ gridColumn: "1 / -1" }}>
                  目标 URL
                  <input
                    value={targetUrl}
                    onChange={(event) => setTargetUrl(event.target.value)}
                    placeholder="https://lab.example.test"
                  />
                  <FieldError message={errors.targetUrl} />
                </label>
              </div>
              <p className="muted" style={{ marginBottom: 0, fontSize: 12 }}>
                目标必须是你拥有或已获授权测试的系统。
              </p>
            </Panel>
          ) : null}
          {step === 1 ? (
            <Panel title="任务意图" icon={MessageSquareText}>
              <label className="field">
                用自然语言描述你希望 Agent 执行的安全测试
                <textarea
                  rows={6}
                  value={missionIntent}
                  onChange={(event) => setMissionIntent(event.target.value)}
                  placeholder="例如：对目标执行 Web 探测，识别并验证 SQLi / XSS，整理可复现证据。"
                />
                <FieldError message={errors.missionIntent} />
              </label>
              <p className="muted" style={{ marginBottom: 0, fontSize: 12 }}>
                意图会成为 Agent 的 mission 上下文，建议包含目标动作、验证范围和交付要求。
              </p>
            </Panel>
          ) : null}
          {step === 2 ? (
            <>
              <Panel title="任务模板" icon={Settings2}>
                <div className="tabs" style={{ marginBottom: 12, borderBottom: 0, paddingBottom: 0 }}>
                  <button
                    className={`tab${template === "default" ? " active" : ""}`}
                    onClick={() => setTemplate("default")}
                  >
                    Default
                  </button>
                  <button
                    className={`tab${template === "scanner-verify" ? " active" : ""}`}
                    onClick={() => setTemplate("scanner-verify")}
                  >
                    Scanner Verify
                  </button>
                  <button
                    className={`tab${template === "toolchain-verify" ? " active" : ""}`}
                    onClick={() => {
                      setTemplate("toolchain-verify");
                      setScannerTools(
                        "web.nikto.scan,web.sqlmap.scan,nuclei.scan,nmap.scan,fscan.scan",
                      );
                      setRequiredCategories("Exposure,OutdatedComponent,SQLi");
                      setToolArgs(
                        JSON.stringify(
                          {
                            "web.sqlmap.scan": {
                              cookie: "PHPSESSID=<session>; security=low",
                            },
                            "nmap.scan": { ports: "80,443" },
                            "fscan.scan": { ports: "80,443" },
                          },
                          null,
                          2,
                        ),
                      );
                    }}
                  >
                    工具链验收
                  </button>
                  <button
                    className={`tab${template === "redteam-orchestration" ? " active" : ""}`}
                    onClick={() => {
                      setTemplate("redteam-orchestration");
                      setScannerTools(
                        "nmap.scan,fscan.scan,nuclei.scan,web.nikto.scan",
                      );
                      setRequiredCategories("Exposure,OutdatedComponent");
                      setMinSeverity("low");
                      setWallClockSeconds("600");
                      setToolArgs(
                        JSON.stringify(
                          {
                            "nmap.scan": { ports: "80,443" },
                            "fscan.scan": { ports: "80,443" },
                          },
                          null,
                          2,
                        ),
                      );
                      setForcedToolArgs("{}");
                    }}
                  >
                    Red Team
                  </button>
                  <button
                    className={`tab${template === "code-audit" ? " active" : ""}`}
                    onClick={() => {
                      setTemplate("code-audit");
                      setScannerTools("code.sast.semgrep,code.secrets.detect");
                      setRequiredCategories("security,HardcodedSecret");
                      setMinSeverity("low");
                      setToolArgs(
                        JSON.stringify(
                          {
                            "code.sast.semgrep": {
                              path: "/workspace/input",
                            },
                            "code.secrets.detect": {
                              path: "/workspace/input",
                            },
                          },
                          null,
                          2,
                        ),
                      );
                      setForcedToolArgs("{}");
                    }}
                  >
                    Code Audit
                  </button>
                </div>
                {template === "default" ? (
                  <p className="muted" style={{ marginBottom: 0 }}>
                    默认模板：由 Agent 自主编排图与角色，不预置扫描器策略，适合开放目标探测。
                  </p>
                ) : template === "toolchain-verify" ||
                  template === "code-audit" ||
                  template === "redteam-orchestration" ? (
                  <div className="form-grid">
                    {template === "code-audit" ? (
                      <p
                        className="muted"
                        style={{ gridColumn: "1 / -1", marginBottom: 0 }}
                      >
                        目标路径会挂载到容器内 /workspace/input，默认扫描
                        /workspace/input；需要扫描子目录时在 Tool args 中修改。
                      </p>
                    ) : null}
                    <label className="field">
                      Required categories（逗号分隔）
                      <input
                        value={requiredCategories}
                        onChange={(event) => setRequiredCategories(event.target.value)}
                        placeholder="Exposure,OutdatedComponent,SQLi"
                      />
                    </label>
                    <label className="field">
                      最低严重级别
                      <select
                        value={minSeverity}
                        onChange={(event) => setMinSeverity(event.target.value)}
                      >
                        <option value="low">low</option>
                        <option value="medium">medium</option>
                        <option value="high">high</option>
                        <option value="critical">critical</option>
                      </select>
                    </label>
                    <label className="field" style={{ gridColumn: "1 / -1" }}>
                      Scanner tools（逗号分隔）
                      <input
                        value={scannerTools}
                        onChange={(event) => setScannerTools(event.target.value)}
                      />
                    </label>
                    <label className="field" style={{ gridColumn: "1 / -1" }}>
                      Tool args defaults（JSON）
                      <textarea
                        rows={4}
                        value={toolArgs}
                        onChange={(event) => setToolArgs(event.target.value)}
                      />
                      <FieldError message={errors.toolArgs} />
                    </label>
                    <label className="field" style={{ gridColumn: "1 / -1" }}>
                      Forced tool args（JSON，覆盖模型参数）
                      <textarea
                        rows={3}
                        value={forcedToolArgs}
                        onChange={(event) =>
                          setForcedToolArgs(event.target.value)
                        }
                        placeholder='{"web.owasp.test":{"check":"rate_limit"}}'
                      />
                      <FieldError message={errors.forcedToolArgs} />
                    </label>
                    <label className="checkbox-label">
                      <input
                        type="checkbox"
                        checked={requireEvidence}
                        onChange={(event) => setRequireEvidence(event.target.checked)}
                      />
                      需要可验证证据
                    </label>
                    <label className="checkbox-label">
                      <input
                        type="checkbox"
                        checked={parallelToolCalls}
                        onChange={(event) =>
                          setParallelToolCalls(event.target.checked)
                        }
                      />
                      并行工具调用
                    </label>
                    <label className="checkbox-label">
                      <input
                        type="checkbox"
                        checked={dedupe}
                        onChange={(event) => setDedupe(event.target.checked)}
                      />
                      发现去重
                    </label>
                  </div>
                ) : (
                  <div className="form-grid">
                    <label className="field">
                      Required categories（逗号分隔）
                      <input
                        value={requiredCategories}
                        onChange={(event) => setRequiredCategories(event.target.value)}
                        placeholder="XSS,SQLi"
                      />
                    </label>
                    <label className="field">
                      最低严重级别
                      <select
                        value={minSeverity}
                        onChange={(event) => setMinSeverity(event.target.value)}
                      >
                        <option value="low">low</option>
                        <option value="medium">medium</option>
                        <option value="high">high</option>
                        <option value="critical">critical</option>
                      </select>
                    </label>
                    <label className="field">
                      Scanner tools（逗号分隔）
                      <input
                        value={scannerTools}
                        onChange={(event) => setScannerTools(event.target.value)}
                        placeholder="web.nikto.scan"
                      />
                    </label>
                    <label className="field">
                      Tool args defaults（JSON）
                      <input
                        value={toolArgs}
                        onChange={(event) => setToolArgs(event.target.value)}
                        placeholder='{"web.sqlmap.scan":{"cookie":"PHPSESSID=..."}}'
                      />
                      <FieldError message={errors.toolArgs} />
                    </label>
                    <label className="field">
                      Forced tool args（JSON，覆盖模型参数）
                      <input
                        value={forcedToolArgs}
                        onChange={(event) =>
                          setForcedToolArgs(event.target.value)
                        }
                        placeholder='{"web.owasp.test":{"check":"security_headers"}}'
                      />
                      <FieldError message={errors.forcedToolArgs} />
                    </label>
                    <label className="checkbox-label">
                      <input
                        type="checkbox"
                        checked={requireEvidence}
                        onChange={(event) => setRequireEvidence(event.target.checked)}
                      />
                      需要可验证证据
                    </label>
                    <label className="checkbox-label">
                      <input
                        type="checkbox"
                        checked={dedupe}
                        onChange={(event) => setDedupe(event.target.checked)}
                      />
                      发现去重
                    </label>
                    <label className="checkbox-label">
                      <input
                        type="checkbox"
                        checked={blockConflicts}
                        onChange={(event) => setBlockConflicts(event.target.checked)}
                      />
                      负向证据阻断
                    </label>
                  </div>
                )}
              </Panel>
              <Panel title="模型与执行" icon={Rocket}>
                <p className="muted" style={{ fontSize: 12, marginBottom: 10 }}>
                  默认使用系统集中配置的模型供应商；也可以选择已登记供应商或自定义覆盖。
                </p>
                <div className="form-grid">
                  <label className="field" style={{ gridColumn: "1 / -1" }}>
                    执行节点
                    <select
                      value={executionNode}
                      onChange={(event) => setExecutionNode(event.target.value)}
                    >
                      <option value="local">local（本机 agent-worker）</option>
                      {((remoteNodes.data as Array<Record<string, unknown>> | undefined) ?? [])
                        .filter((node) => node.status === "online")
                        .map((node) => (
                          <option key={String(node.node_id)} value={String(node.node_id)}>
                            {String(node.node_id)}（{String(node.status)}）
                          </option>
                        ))}
                    </select>
                    <FieldError message={errors.executionNode} />
                  </label>
                  {executionNode && executionNode !== "local" ? (
                    <>
                      <label className="field">
                        等待远端结果（秒）
                        <input
                          type="number"
                          min={0}
                          value={executionWaitSeconds}
                          onChange={(event) =>
                            setExecutionWaitSeconds(event.target.value)
                          }
                          placeholder="30"
                        />
                      </label>
                      <label className="field">
                        轮询间隔（秒）
                        <input
                          type="number"
                          min={1}
                          value={executionPollInterval}
                          onChange={(event) =>
                            setExecutionPollInterval(event.target.value)
                          }
                          placeholder="2"
                        />
                      </label>
                      <label className="field" style={{ gridColumn: "1 / -1" }}>
                        远端探测命令（可选，留空自动按工具生成）
                        <input
                          value={executionCommand}
                          onChange={(event) =>
                            setExecutionCommand(event.target.value)
                          }
                          placeholder="curl -sS http://target/ | head -c 200"
                        />
                      </label>
                      <label className="checkbox-label">
                        <input
                          type="checkbox"
                          checked={executionStrict}
                          onChange={(event) =>
                            setExecutionStrict(event.target.checked)
                          }
                        />
                        远端结果缺失即判失败（严格模式）
                      </label>
                    </>
                  ) : null}
                  <label className="field" style={{ gridColumn: "1 / -1" }}>
                    供应商
                    <select
                      value={providerChoice}
                      onChange={(event) => {
                        const value = event.target.value;
                        setProviderChoice(value);
                        const row = providerRows.find(
                          (item) => item.provider_id === value,
                        );
                        if (row) {
                          setProviderEndpoint(String(row.endpoint ?? ""));
                          setProviderModel(String(row.model ?? ""));
                          setApiKeyRef(
                            String(
                              ((row.config ?? {}) as Record<string, unknown>)
                                .api_key_ref ?? "",
                            ),
                          );
                        }
                      }}
                    >
                      <option value="default">使用系统默认供应商</option>
                      {providerRows.map((row) => (
                        <option key={String(row.provider_id)} value={String(row.provider_id)}>
                          {String(row.provider_id)} · {String(row.model)}
                        </option>
                      ))}
                      <option value="custom">自定义（手动填写）</option>
                    </select>
                  </label>
                  <label className="field">
                    Provider endpoint（可选）
                    <input
                      value={providerEndpoint}
                      onChange={(event) => setProviderEndpoint(event.target.value)}
                      placeholder="https://api.deepseek.com/v1"
                      disabled={providerChoice !== "custom"}
                    />
                  </label>
                  <label className="field">
                    Provider model（可选）
                    <input
                      value={providerModel}
                      onChange={(event) => setProviderModel(event.target.value)}
                      placeholder="deepseek-v4-flash"
                      list="mission-provider-models"
                      disabled={providerChoice === "default"}
                    />
                    <datalist id="mission-provider-models">
                      {providerModelOptions.map((model) => (
                        <option key={model} value={model} />
                      ))}
                    </datalist>
                    <button
                      className="btn"
                      type="button"
                      onClick={() => fetchModels.mutate()}
                      disabled={
                        fetchModels.isPending ||
                        providerChoice === "default" ||
                        !providerEndpoint.trim()
                      }
                      title={
                        providerChoice === "default"
                          ? "默认供应商下无需获取模型列表"
                          : !providerEndpoint.trim()
                            ? "请先填写 Provider endpoint"
                            : fetchModels.isPending
                              ? "正在获取..."
                              : undefined
                      }
                      style={{ marginTop: 6 }}
                    >
                      {fetchModels.isPending ? "获取中..." : "获取模型列表"}
                    </button>
                  </label>
                  <label className="field">
                    API key ref（可选）
                    <input
                      value={apiKeyRef}
                      onChange={(event) => setApiKeyRef(event.target.value)}
                      placeholder="env:DEEPSEEK_API_KEY"
                      disabled={providerChoice !== "custom"}
                    />
                  </label>
                  <label className="field">
                    推理强度
                    <select
                      value={reasoningEffort}
                      onChange={(event) => setReasoningEffort(event.target.value)}
                      disabled={providerChoice === "default"}
                    >
                      <option value="none">跟随供应商默认</option>
                      <option value="low">low</option>
                      <option value="medium">medium</option>
                      <option value="high">high</option>
                    </select>
                  </label>
                  <label className="field">
                    Provider 重试次数
                    <input
                      type="number"
                      min={1}
                      max={10}
                      value={providerRetries}
                      onChange={(event) => setProviderRetries(event.target.value)}
                      disabled={providerChoice === "default"}
                    />
                  </label>
                  <label className="field">
                    最大轮次
                    <input
                      type="number"
                      min={1}
                      value={maxTurns}
                      onChange={(event) => setMaxTurns(event.target.value)}
                    />
                    <FieldError message={errors.maxTurns} />
                  </label>
                  <label className="field">
                    预算策略
                    <select
                      value={budgetPolicy}
                      onChange={(event) => setBudgetPolicy(event.target.value)}
                    >
                      <option value="continue">continue（不因轮次/预算提前截断）</option>
                      <option value="pause_and_resume">pause_and_resume（默认）</option>
                    </select>
                  </label>
                  <label className="field">
                    瞬时错误重试次数
                    <input
                      type="number"
                      min={0}
                      max={10}
                      value={retryTransient}
                      onChange={(event) => setRetryTransient(event.target.value)}
                    />
                  </label>
                  <label className="field">
                    任务时长上限（秒，0 为不限）
                    <input
                      type="number"
                      min={0}
                      value={wallClockSeconds}
                      onChange={(event) => setWallClockSeconds(event.target.value)}
                    />
                  </label>
                  <label className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={streaming}
                      onChange={(event) => setStreaming(event.target.checked)}
                    />
                    Streaming deltas
                  </label>
                </div>
              </Panel>
              <Panel title="检索与记忆" icon={Database}>
                <div className="form-grid">
                  <label className="field">
                    知识检索级别
                    <select
                      value={retrievalLevel}
                      onChange={(event) => setRetrievalLevel(event.target.value)}
                    >
                      <option value="lexical">lexical（BM25）</option>
                      <option value="qdrant_hybrid">qdrant_hybrid</option>
                      <option value="embedding_rerank">embedding + rerank</option>
                    </select>
                  </label>
                  <label className="field">
                    记忆检索级别
                    <select
                      value={memoryRetrievalLevel}
                      onChange={(event) =>
                        setMemoryRetrievalLevel(event.target.value)
                      }
                    >
                      <option value="hybrid">hybrid（向量 + 词法）</option>
                      <option value="lexical">lexical</option>
                    </select>
                  </label>
                  <label className="field">
                    记忆 token 预算
                    <input
                      type="number"
                      min={0}
                      value={memoryTokenBudget}
                      onChange={(event) =>
                        setMemoryTokenBudget(event.target.value)
                      }
                      placeholder="2000"
                    />
                    <FieldError message={errors.memoryTokenBudget} />
                  </label>
                  <label className="field">
                    记忆最大条数
                    <input
                      type="number"
                      min={1}
                      value={memoryLimit}
                      onChange={(event) => setMemoryLimit(event.target.value)}
                      placeholder="20"
                    />
                    <FieldError message={errors.memoryLimit} />
                  </label>
                  <label className="field">
                    Loop Preset
                    <select
                      value={loopPreset}
                      onChange={(event) => {
                        const presetId = event.target.value;
                        setLoopPreset(presetId);
                        if (!presetId) {
                          setLoopProfiles("{}");
                          return;
                        }
                        const preset = loopPresetRows.find(
                          (item) => String(item.preset_id) === presetId,
                        );
                        const overrides = (preset?.loop_overrides ??
                          {}) as Record<string, unknown>;
                        setLoopProfiles(
                          JSON.stringify(overrides, null, 2),
                        );
                      }}
                    >
                      <option value="">不应用预设</option>
                      {loopPresetRows.map((preset) => (
                        <option
                          key={String(preset.preset_id)}
                          value={String(preset.preset_id)}
                        >
                          {String(preset.label)} - {String(preset.description ?? "").slice(0, 48)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field">
                    Loop Profiles 覆盖 (JSON)
                    <textarea
                      rows={4}
                      value={loopProfiles}
                      onChange={(event) => setLoopProfiles(event.target.value)}
                      placeholder='{"discovery":{"knowledge_query":["custom_recon"],"allowed_skills":["web-discovery"]}}'
                    />
                    <FieldError message={errors.loopProfiles} />
                  </label>
                </div>
              </Panel>
            </>
          ) : null}
          {step === 3 ? (
            <Panel title="确认并创建" icon={Rocket}>
              <Notice tone="info">请核对配置，创建后可在控制台继续会话指挥。</Notice>
              <pre className="result-box" style={{ marginBottom: 12 }}>
                {JSON.stringify(
                  {
                    项目: projectName,
                    任务: missionName,
                    目标: targetUrl,
                    意图: missionIntent,
                    模板: template,
                    最大轮次: Number(maxTurns) || 5,
                    流式: streaming,
                    执行节点: executionNode === "local" ? "local" : executionNode,
                    预算策略: budgetPolicy,
                    推理强度: reasoningEffort,
                    知识检索: retrievalLevel,
                    记忆检索: memoryRetrievalLevel,
                    记忆预算: Number(memoryTokenBudget) || 2000,
                    记忆条数: Number(memoryLimit) || 20,
                    "Loop Profiles": loopPreset
                      ? `${loopPreset} / ${loopProfiles}`
                      : loopProfiles.trim()
                        ? loopProfiles
                        : "默认",
                    Provider: providerEndpoint && providerModel
                      ? `${providerModel} @ ${providerEndpoint}`
                      : "默认",
                  },
                  null,
                  2,
                )}
              </pre>
              <button
                className="btn btn-primary"
                onClick={() => create.mutate()}
                disabled={create.isPending}
              >
                {create.isPending ? "创建中..." : "创建项目 / 目标 / 任务"}
              </button>
            </Panel>
          ) : null}
          <div className="btn-group">
            {step > 0 ? (
              <button className="btn" onClick={back}>
                <ChevronLeft className="" />
                上一步
              </button>
            ) : null}
            {step < STEPS.length - 1 ? (
              <button className="btn btn-primary" onClick={next}>
                下一步
                <ChevronRight className="" />
              </button>
            ) : null}
          </div>
        </div>
        <div className="stack">
          <Panel title="当前配置" icon={Settings2}>
            <div className="memory-summary">
              <span>步骤 {step + 1}/{STEPS.length}</span>
              <span>模板 {template}</span>
              <span>轮次 {maxTurns || 5}</span>
              <span>{streaming ? "流式" : "非流式"}</span>
              <span>
                {executionNode === "local" ? "本地执行" : `远端 ${executionNode}`}
              </span>
              <span>{providerModel.trim() || "默认模型"}</span>
            </div>
            <p className="muted" style={{ marginBottom: 0, fontSize: 12 }}>
              {STEPS[step].label}：{step === 0
                ? "确认项目、任务与授权目标。"
                : step === 1
                  ? "描述 Agent 要执行的动作与验证范围。"
                  : step === 2
                    ? "选择模板、模型与执行参数。"
                    : "创建资源并启动运行。"}
            </p>
          </Panel>
          {Object.keys(created).length > 0 ? (
            <Panel title="已创建资源" icon={CheckCircle2}>
              <Notice tone="ok">创建成功，可以启动运行。</Notice>
              <pre className="result-box" style={{ marginBottom: 10 }}>
                {JSON.stringify(created, null, 2)}
              </pre>
              <div className="btn-group">
                <button
                  className="btn btn-primary"
                  onClick={() => startRun.mutate()}
                  disabled={startRun.isPending || !missionId}
                >
                  <CirclePlay className="" />
                  {startRun.isPending ? "启动中..." : "启动运行"}
                </button>
                {created.run ? (
                  <button
                    className="btn"
                    onClick={() => {
                      navigate("/cockpit");
                      setSelectedRunId(created.run!);
                    }}
                  >
                    <ExternalLink className="" />
                    打开控制台
                  </button>
                ) : null}
              </div>
              {startRun.isError ? (
                <div style={{ marginTop: 10 }}>
                  <Notice tone="error">{String(startRun.error)}</Notice>
                </div>
              ) : null}
            </Panel>
          ) : null}
        </div>
      </div>
    </section>
  );
}
