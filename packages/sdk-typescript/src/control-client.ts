export interface Project {
  project_id: string;
  name: string;
  owner?: string;
  created_at: string;
}

export interface TargetProfile {
  target_id: string;
  project_id: string;
  url: string;
  allowed: string[];
  excluded: string[];
  authorization: string;
  created_at: string;
}

export interface Mission {
  mission_id: string;
  project_id: string;
  name: string;
  spec: Record<string, unknown>;
  created_at: string;
}

export interface RunState {
  run_id: string;
  mission_id: string;
  source_run_id?: string | null;
  status: string;
  event_count: number;
  observations: unknown[];
  stop_reason: string | null;
  created_at: string;
}

export interface AgentEvent {
  schema_version: number;
  event_id: string;
  event_type: string;
  stream_id: string;
  run_id: string;
  actor: string;
  occurred_at: string;
  sequence: number | null;
  payload: Record<string, unknown>;
}

export interface ApprovalRequest {
  approval_id: string;
  run_id: string;
  tool_ref: string;
  risk_level: string;
  state: string;
  policy_rule: string;
  reason: string;
  requested_at: string;
  decided_at: string | null;
  decided_by: string | null;
  budget_reserved: number;
}

export interface WebObservation {
  request_id: string;
  web_session_id: string;
  proxy_session_id: string;
  method: string;
  url: string;
  endpoint: string;
  status_code: number;
  request_headers: Record<string, string>;
  response_headers: Record<string, string>;
  request_body: string;
  response_body: string;
  content_type: string;
  request_size: number;
  response_size: number;
  artifact_ref: string;
  redacted: boolean;
  truncated: boolean;
  replay_proof?: {
    request_id: string;
    request_fingerprint: string;
    response_fingerprint: string;
    replayed_status: number;
    replayed_at: string;
    matched: boolean;
  };
}

export interface Finding {
  finding_id: string;
  run_id: string;
  target_ref: string;
  vuln_category: string;
  endpoint: string;
  param: string;
  status: string;
  severity?: string;
  asset_id?: string;
  remediation?: string;
  fingerprint: string;
  evidence_ids: string[];
  notes: string;
  created_at: string;
  updated_at: string;
  retest_proof: Record<string, unknown>;
}

export interface MergedFinding {
  fingerprint: string;
  target_ref: string;
  vuln_category: string;
  endpoint: string;
  status: string;
  primary_finding_id: string;
  evidence_ids: string[];
  source_finding_ids: string[];
  duplicate_count: number;
}

export class ControlClient {
  private token: string | null = null;

  constructor(private readonly baseUrl: string) {}

  setToken(token: string | null): void {
    this.token = token;
  }

  private authToken(): string | null {
    if (this.token) {
      return this.token;
    }
    if (typeof localStorage !== "undefined") {
      return localStorage.getItem("veridix_control_token");
    }
    return null;
  }

  async createProject(name: string): Promise<Project> {
    return this.request<Project>("POST", "/api/v1/projects", { name });
  }

  async createTarget(
    projectId: string,
    url: string,
    options: { allowed?: string[]; excluded?: string[] } = {},
  ): Promise<TargetProfile> {
    return this.request<TargetProfile>("POST", `/api/v1/projects/${projectId}/targets`, {
      url,
      allowed: options.allowed ?? [],
      excluded: options.excluded ?? [],
    });
  }

  async createMission(
    projectId: string,
    name: string,
    spec: Record<string, unknown> = {},
  ): Promise<Mission> {
    return this.request<Mission>("POST", "/api/v1/missions", {
      project_id: projectId,
      name,
      spec,
    });
  }

  async getMission(missionId: string): Promise<Mission> {
    return this.request<Mission>(
      "GET",
      `/api/v1/missions/${missionId}`,
    );
  }

  async listProviders(): Promise<Array<Record<string, unknown>>> {
    return this.request<Array<Record<string, unknown>>>(
      "GET",
      "/api/v1/runtime/providers",
    );
  }

  async listProviderPresets(): Promise<Array<Record<string, unknown>>> {
    return this.request<Array<Record<string, unknown>>>(
      "GET",
      "/api/v1/providers/presets",
    );
  }

  async listProjects(): Promise<Project[]> {
    return this.request<Project[]>("GET", "/api/v1/projects");
  }

  async registerProvider(body: {
    provider_id: string;
    model: string;
    endpoint: string;
    status?: string;
    api_key_ref?: string;
    backend?: string;
    litellm_provider?: string;
    timeout_seconds?: number;
    thinking_mode?: string;
    reasoning_effort?: string;
    retries?: number;
    streaming?: boolean;
    max_tokens?: number;
    headers?: Record<string, string>;
  }): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "POST",
      "/api/v1/runtime/providers",
      body,
    );
  }

  async deleteProvider(providerId: string): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "DELETE",
      `/api/v1/runtime/providers/${providerId}`,
    );
  }

  async getRetrievalSettings(): Promise<Record<string, unknown> | null> {
    return this.request<Record<string, unknown> | null>(
      "GET",
      "/api/v1/settings/retrieval",
    );
  }

  async setRetrievalSettings(
    body: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "POST",
      "/api/v1/settings/retrieval",
      body,
    );
  }

  async testRetrievalSettings(
    body: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "POST",
      "/api/v1/settings/retrieval/test",
      body,
    );
  }

  async probeProvider(body: {
    provider_id: string;
    endpoint: string;
    model: string;
    api_key_ref?: string;
    backend?: string;
    litellm_provider?: string;
    timeout_seconds?: number;
  }): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "POST",
      "/api/v1/providers/probe",
      body,
    );
  }

  async fetchProviderModels(body: {
    provider_id: string;
    endpoint: string;
    model: string;
    api_key_ref?: string;
    backend?: string;
    litellm_provider?: string;
    timeout_seconds?: number;
  }): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "POST",
      "/api/v1/providers/models",
      body,
    );
  }

  async getProviderDefault(): Promise<Record<string, unknown> | null> {
    return this.request<Record<string, unknown> | null>(
      "GET",
      "/api/v1/settings/provider-default",
    );
  }

  async setProviderDefault(body: {
    provider_id: string;
    endpoint: string;
    model: string;
    api_key_ref?: string;
  }): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "POST",
      "/api/v1/settings/provider-default",
      body,
    );
  }

  async listRunners(): Promise<Array<Record<string, unknown>>> {
    return this.request<Array<Record<string, unknown>>>(
      "GET",
      "/api/v1/runtime/runners",
    );
  }

  async registerRunner(body: {
    runner_id: string;
    kind: string;
    status?: string;
  }): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "POST",
      "/api/v1/runtime/runners",
      body,
    );
  }

  async registerSkill(body: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "POST",
      "/api/v1/runtime/skills",
      body,
    );
  }

  async deleteSkill(skillRef: string): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "DELETE",
      `/api/v1/runtime/skills/${encodeURIComponent(skillRef)}`,
    );
  }

  async registerMcp(body: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "POST",
      "/api/v1/runtime/mcp",
      body,
    );
  }

  async listMcpPresets(): Promise<Array<Record<string, unknown>>> {
    return this.request<Array<Record<string, unknown>>>(
      "GET",
      "/api/v1/runtime/mcp/presets",
    );
  }

  async deleteMcp(serverId: string): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "DELETE",
      `/api/v1/runtime/mcp/${encodeURIComponent(serverId)}`,
    );
  }

  async testMcp(serverId: string): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "POST",
      `/api/v1/runtime/mcp/${encodeURIComponent(serverId)}/test`,
    );
  }

  async registerTool(body: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "POST",
      "/api/v1/runtime/tools",
      body,
    );
  }

  async listToolPacks(): Promise<Array<Record<string, unknown>>> {
    return this.request<Array<Record<string, unknown>>>(
      "GET",
      "/api/v1/runtime/tool-packs",
    );
  }

  async getSkill(skillRef: string): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "GET",
      `/api/v1/runtime/skills/${encodeURIComponent(skillRef)}`,
    );
  }

  async listSkills(): Promise<Array<Record<string, unknown>>> {
    return this.request<Array<Record<string, unknown>>>(
      "GET",
      "/api/v1/runtime/skills",
    );
  }

  async deleteTool(toolRef: string): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "DELETE",
      `/api/v1/runtime/tools/${encodeURIComponent(toolRef)}`,
    );
  }

  async listAssets(projectId?: string): Promise<Array<Record<string, unknown>>> {
    return this.request<Array<Record<string, unknown>>>(
      "GET",
      `/api/v1/assets${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`,
    );
  }

  async listAssetLifecycle(): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "GET",
      "/api/v1/assets/lifecycle",
    );
  }

  async createAsset(body: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>("POST", "/api/v1/assets", body);
  }

  async updateAsset(
    assetId: string,
    body: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "PATCH",
      `/api/v1/assets/${assetId}`,
      body,
    );
  }

  async deleteAsset(assetId: string): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "DELETE",
      `/api/v1/assets/${assetId}`,
    );
  }

  async assetFindings(assetId: string): Promise<Array<Record<string, unknown>>> {
    return this.request<Array<Record<string, unknown>>>(
      "GET",
      `/api/v1/assets/${assetId}/findings`,
    );
  }

  async importTargetAssets(projectId: string): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "POST",
      `/api/v1/projects/${projectId}/assets/import`,
    );
  }

  async listSessions(): Promise<Array<Record<string, unknown>>> {
    return this.request<Array<Record<string, unknown>>>(
      "GET",
      "/api/v1/sessions",
    );
  }

  async updateSession(
    sessionId: string,
    body: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "PATCH",
      `/api/v1/sessions/${sessionId}`,
      body,
    );
  }

  async deleteSession(sessionId: string): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "DELETE",
      `/api/v1/sessions/${sessionId}`,
    );
  }

  async listVulnerabilities(params?: {
    project_id?: string;
    status?: string;
    severity?: string;
  }): Promise<Array<Record<string, unknown>>> {
    const query = new URLSearchParams();
    if (params?.project_id) {
      query.set("project_id", params.project_id);
    }
    if (params?.status) {
      query.set("status", params.status);
    }
    if (params?.severity) {
      query.set("severity", params.severity);
    }
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return this.request<Array<Record<string, unknown>>>(
      "GET",
      `/api/v1/vulnerabilities${suffix}`,
    );
  }

  async updateVulnerability(
    findingId: string,
    body: Record<string, unknown>,
  ): Promise<Finding> {
    return this.request<Finding>(
      "PATCH",
      `/api/v1/vulnerabilities/${findingId}`,
      body,
    );
  }

  async appendFindingNote(
    findingId: string,
    note: string,
  ): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "POST",
      `/api/v1/findings/${encodeURIComponent(findingId)}/notes`,
      { note },
    );
  }

  async riskSummary(projectId?: string): Promise<Record<string, unknown>> {
    const suffix = projectId
      ? `?project_id=${encodeURIComponent(projectId)}`
      : "";
    return this.request<Record<string, unknown>>(
      "GET",
      `/api/v1/risk${suffix}`,
    );
  }

  async listAuditLogs(body?: {
    limit?: number;
    action?: string;
    actor?: string;
  }): Promise<Array<Record<string, unknown>>> {
    const params = new URLSearchParams();
    if (body?.limit) params.set("limit", String(body.limit));
    if (body?.action) params.set("action", body.action);
    if (body?.actor) params.set("actor", body.actor);
    const suffix = params.toString() ? `?${params.toString()}` : "";
    return this.request<Array<Record<string, unknown>>>(
      "GET",
      `/api/v1/audit-logs${suffix}`,
    );
  }

  async getEvidenceGate(runId: string): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "GET",
      `/api/v1/runs/${encodeURIComponent(runId)}/evidence-gate`,
    );
  }

  async listRoleTemplates(): Promise<Array<Record<string, unknown>>> {
    return this.request<Array<Record<string, unknown>>>(
      "GET",
      "/api/v1/runtime/role-templates",
    );
  }

  async listLoopProfiles(): Promise<Record<string, Record<string, unknown>>> {
    return this.request<Record<string, Record<string, unknown>>>(
      "GET",
      "/api/v1/runtime/loop-profiles",
    );
  }

  async listLoopPresets(): Promise<Record<string, Record<string, unknown>>> {
    return this.request<Record<string, Record<string, unknown>>>(
      "GET",
      "/api/v1/runtime/loop-presets",
    );
  }

  async saveRoleTemplate(body: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "POST",
      "/api/v1/runtime/role-templates",
      body,
    );
  }

  async deleteRoleTemplate(templateId: string): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "DELETE",
      `/api/v1/runtime/role-templates/${templateId}`,
    );
  }

  async startRun(missionId: string, idempotencyKey: string): Promise<RunState> {
    return this.request<RunState>("POST", `/api/v1/missions/${missionId}/runs`, {
      mission_id: missionId,
      idempotency_key: idempotencyKey,
    });
  }

  async runCommand(
    runId: string,
    command: "pause" | "resume" | "cancel",
    idempotencyKey: string,
  ): Promise<RunState> {
    return this.request<RunState>("POST", `/api/v1/runs/${runId}/${command}`, {
      idempotency_key: idempotencyKey,
    });
  }

  async sendMessage(
    runId: string,
    message: string,
    idempotencyKey: string,
    operator = "web-operator",
  ): Promise<RunState> {
    return this.request<RunState>(
      "POST",
      `/api/v1/runs/${runId}/message`,
      {
        message,
        idempotency_key: idempotencyKey,
        operator,
      },
    );
  }

  async forkRun(
    runId: string,
    idempotencyKey: string,
  ): Promise<RunState> {
    return this.request<RunState>(
      "POST",
      `/api/v1/runs/${runId}/fork`,
      { idempotency_key: idempotencyKey },
    );
  }

  async takeoverRun(
    runId: string,
    takenBy: string,
    idempotencyKey: string,
    reason = "",
  ): Promise<RunState> {
    return this.request<RunState>(
      "POST",
      `/api/v1/runs/${runId}/takeover`,
      { idempotency_key: idempotencyKey, taken_by: takenBy, reason },
    );
  }

  async claimRun(
    runId: string,
    workerId: string,
    idempotencyKey: string,
  ): Promise<RunState> {
    return this.request<RunState>(
      "POST",
      `/api/v1/runs/${runId}/claim`,
      { worker_id: workerId, idempotency_key: idempotencyKey },
    );
  }

  async finishRun(
    runId: string,
    outcome: "succeeded" | "failed",
    idempotencyKey: string,
    options: { stopReason?: string; summary?: string } = {},
  ): Promise<RunState> {
    return this.request<RunState>(
      "POST",
      `/api/v1/runs/${runId}/finish`,
      {
        outcome,
        idempotency_key: idempotencyKey,
        stop_reason: options.stopReason ?? "",
        summary: options.summary ?? "",
      },
    );
  }

  async getRun(runId: string): Promise<RunState> {
    return this.request<RunState>("GET", `/api/v1/runs/${runId}`);
  }

  async listRuns(): Promise<RunState[]> {
    return this.request<RunState[]>("GET", "/api/v1/runs");
  }

  async deleteProject(projectId: string): Promise<{ deleted: boolean }> {
    return this.request<{ deleted: boolean }>(
      "DELETE",
      `/api/v1/projects/${projectId}`,
    );
  }

  async getEvents(runId: string, after = 0): Promise<AgentEvent[]> {
    return this.request<AgentEvent[]>(
      "GET",
      `/api/v1/runs/${runId}/events?after=${after}`,
    );
  }

  async getWebObservations(runId: string): Promise<WebObservation[]> {
    return this.request<WebObservation[]>(
      "GET",
      `/api/v1/runs/${runId}/web-observations`,
    );
  }

  async upsertWebObservations(
    runId: string,
    observations: WebObservation[],
  ): Promise<{ stored: number }> {
    return this.request<{ stored: number }>(
      "POST",
      `/api/v1/runs/${runId}/web-observations`,
      { observations },
    );
  }

  async listFindings(runId: string): Promise<Finding[]> {
    return this.request<Finding[]>(
      "GET",
      `/api/v1/runs/${runId}/findings`,
    );
  }

  async listMergedFindings(runId: string): Promise<MergedFinding[]> {
    return this.request<MergedFinding[]>(
      "GET",
      `/api/v1/runs/${runId}/findings/merged`,
    );
  }

  async submitFinding(
    runId: string,
    body: {
      target_ref: string;
      vuln_category: string;
      endpoint: string;
      param?: string;
      notes?: string;
    },
  ): Promise<Finding> {
    return this.request<Finding>(
      "POST",
      `/api/v1/runs/${runId}/findings`,
      body,
    );
  }

  async supportFinding(findingId: string): Promise<Finding> {
    return this.request<Finding>(
      "POST",
      `/api/v1/findings/${findingId}/support`,
    );
  }

  async verifyFinding(findingId: string, oracle: string): Promise<Finding> {
    return this.request<Finding>(
      "POST",
      `/api/v1/findings/${findingId}/verify`,
      { oracle },
    );
  }

  async reviewFinding(
    findingId: string,
    decision: string,
    decidedBy: string,
  ): Promise<Finding> {
    return this.request<Finding>(
      "POST",
      `/api/v1/findings/${findingId}/review`,
      { decision, decided_by: decidedBy },
    );
  }

  async retestFinding(
    findingId: string,
    proof: Record<string, unknown>,
  ): Promise<Finding> {
    return this.request<Finding>(
      "POST",
      `/api/v1/findings/${findingId}/retest`,
      { proof },
    );
  }

  async health(): Promise<Record<string, string>> {
    return this.request<Record<string, string>>("GET", "/healthz");
  }

  async listApprovals(runId: string): Promise<ApprovalRequest[]> {
    return this.request<ApprovalRequest[]>(
      "GET",
      `/api/v1/runs/${runId}/approvals`,
    );
  }

  async decideApproval(
    approvalId: string,
    approved: boolean,
    decidedBy: string,
  ): Promise<ApprovalRequest> {
    return this.request<ApprovalRequest>(
      "POST",
      `/api/v1/approvals/${approvalId}/decide`,
      { approved, decided_by: decidedBy },
    );
  }

  async requestPublic(path: string): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>("GET", path);
  }

  async requestJson<T>(
    method: string,
    path: string,
    body?: unknown,
  ): Promise<T> {
    return this.request<T>(method, path, body);
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown,
  ): Promise<T> {
    const retryable =
      method === "GET" ||
      (typeof body === "object" &&
        body !== null &&
        ("idempotency_key" in body || "event_id" in body));
    const attempts = retryable ? 3 : 1;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try {
        const response = await fetch(`${this.baseUrl}${path}`, {
          method,
          headers: (() => {
            const headers: Record<string, string> =
              body !== undefined ? { "Content-Type": "application/json" } : {};
            const token = this.authToken();
            if (token) {
              headers["Authorization"] = `Bearer ${token}`;
            }
            return headers;
          })(),
          body: body !== undefined ? JSON.stringify(body) : undefined,
          signal: AbortSignal.timeout(60_000),
        });
        const payload = (await response.json()) as T & { detail?: string };
        if (!response.ok) {
          throw new Error(payload.detail ?? `HTTP ${response.status}`);
        }
        return payload;
      } catch (error) {
        const transient =
          error instanceof TypeError ||
          (error instanceof DOMException && error.name === "TimeoutError");
        if (transient && attempt + 1 < attempts) {
          await new Promise((resolve) =>
            setTimeout(resolve, 400 * (attempt + 1)),
          );
          continue;
        }
        throw error;
      }
    }
    throw new Error("request failed");
  }
}
