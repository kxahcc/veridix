import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BrainCircuit,
  Eraser,
  Pencil,
  RefreshCw,
  Search,
  Trash2,
} from "lucide-react";
import { CONTROL_URL, control } from "../api.js";
import { Loading } from "../components/Status.js";
import {
  Badge,
  EmptyState,
  Kpi,
  Notice,
  Panel,
  SyncStamp,
} from "../components/ui.js";

type MemoryFact = {
  fact_id: string;
  subject: string;
  predicate: string;
  value: string;
  target?: string;
  source_refs?: string[];
  confidence?: number;
  trust?: string;
  observed_at?: string;
  expires_at?: string | null;
  status?: string;
  metadata?: Record<string, unknown>;
};

type MemoryPayload = {
  project_id?: string;
  snapshot?: {
    total_facts?: number;
    active?: number;
    conflict?: number;
    stale?: number;
  };
  facts?: MemoryFact[];
  summaries?: Array<Record<string, unknown>>;
};

function statusTone(status: string | undefined) {
  if (status === "active") return "ok";
  if (status === "conflict") return "danger";
  return "warn";
}

export function MemoryPage() {
  const queryClient = useQueryClient();
  const [subjectFilter, setSubjectFilter] = useState("");
  const [fixSubject, setFixSubject] = useState("/admin");
  const [fixPredicate, setFixPredicate] = useState("accepts_role");
  const [fixValue, setFixValue] = useState("");
  const [fixReason, setFixReason] = useState("verified_by_admin");
  const [recordSubject, setRecordSubject] = useState("/admin");
  const [recordPredicate, setRecordPredicate] = useState("accepts_role");
  const [recordValue, setRecordValue] = useState("");
  const [recordTarget, setRecordTarget] = useState("");
  const [recordExpires, setRecordExpires] = useState("");
  const [successMessage, setSuccessMessage] = useState("");

  const memory = useQuery({
    queryKey: ["project-memory"],
    queryFn: () =>
      control.requestPublic(
        "/api/v1/memory?project_id=default&include_stale=true&limit=200",
      ) as Promise<MemoryPayload>,
    refetchInterval: 5000,
  });
  const data = memory.data as MemoryPayload | undefined;
  const facts = (data?.facts ?? []).filter((fact) =>
    subjectFilter
      ? fact.subject.includes(subjectFilter) ||
        fact.predicate.includes(subjectFilter)
      : true,
  );
  const snapshot = data?.snapshot ?? {};

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["project-memory"] });
  };

  const fix = useMutation({
    mutationFn: async () => {
      const response = await fetch(`${CONTROL_URL}/api/v1/memory/fix`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          subject: fixSubject,
          predicate: fixPredicate,
          value: fixValue,
          reason: fixReason,
        }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    },
    onSuccess: () => {
      invalidate();
      setFixValue("");
      setSuccessMessage("记忆事实已修正并写入固定记录");
    },
  });

  const record = useMutation({
    mutationFn: async () => {
      const response = await fetch(
        `${CONTROL_URL}/api/v1/memory/record`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            subject: recordSubject,
            predicate: recordPredicate,
            value: recordValue,
            target: recordTarget,
            trust: "user_approved",
            expires_in_seconds: recordExpires
              ? Number(recordExpires)
              : null,
          }),
        },
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    },
    onSuccess: () => {
      invalidate();
      setRecordValue("");
      setSuccessMessage("记忆事实已新增");
    },
  });

  const forget = useMutation({
    mutationFn: async (factId: string) => {
      const response = await fetch(
        `${CONTROL_URL}/api/v1/memory/${factId}/forget`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: "web_operator_forget" }),
        },
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    },
    onSuccess: () => {
      invalidate();
      setSuccessMessage("记忆事实已遗忘");
    },
  });

  const clearAll = useMutation({
    mutationFn: async () => {
      const response = await fetch(`${CONTROL_URL}/api/v1/memory/clear`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "web_operator_clear" }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    },
    onSuccess: () => {
      invalidate();
      setSuccessMessage("项目记忆已清空");
    },
  });

  return (
    <section>
      <header className="page-head">
        <div className="page-head-copy">
          <p className="page-eyebrow">Project Memory</p>
          <h1>项目记忆</h1>
          <p className="page-sub">
            查看、修正、遗忘或清空跨会话沉淀的事实，这些内容会投影进 Agent
            的下一步上下文。
          </p>
          <SyncStamp dataUpdatedAt={memory.dataUpdatedAt} />
        </div>
      </header>

      <div className="kpi-grid">
        <Kpi label="事实总数" value={snapshot.total_facts ?? 0} />
        <Kpi label="活跃" value={snapshot.active ?? 0} tone="ok" />
        <Kpi label="冲突" value={snapshot.conflict ?? 0} tone="danger" />
        <Kpi label="过期" value={snapshot.stale ?? 0} tone="warn" />
      </div>

      {successMessage ? (
        <Notice tone="ok">{successMessage}</Notice>
      ) : null}

      <div className="memory-grid">
        <Panel
          title="事实列表"
          icon={BrainCircuit}
          actions={
            <>
              <span className="toolbar" style={{ display: "inline-flex", marginBottom: 0 }}>
                <Search className="" style={{ width: 14, height: 14 }} />
                <input
                  aria-label="筛选主题或谓词"
                  placeholder="筛选主题/谓词"
                  value={subjectFilter}
                  onChange={(event) => setSubjectFilter(event.target.value)}
                />
              </span>
              <button
                className="btn"
                onClick={() => void memory.refetch()}
                disabled={memory.isFetching}
                title="刷新记忆"
              >
                <RefreshCw className="" style={{ width: 14, height: 14 }} />
              </button>
            </>
          }
        >
          {memory.isLoading ? <Loading /> : null}
          {memory.isError ? (
            <Notice tone="error">{String(memory.error)}</Notice>
          ) : null}
          {!memory.isLoading && !memory.isError && facts.length === 0 ? (
            <EmptyState
              title="暂无记忆事实"
              description="Agent 完成真实工具调用后会在这里沉淀跨会话事实。"
              action={
                <button
                  className="btn btn-sm"
                  onClick={() =>
                    document
                      .getElementById("memory-record-form")
                      ?.scrollIntoView({ behavior: "smooth" })
                  }
                >
                  新增事实
                </button>
              }
            />
          ) : null}
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>状态</th>
                  <th>主题</th>
                  <th>谓词</th>
                  <th>值</th>
                  <th>来源</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {facts.map((fact) => (
                  <tr key={fact.fact_id}>
                    <td>
                      <Badge value={statusTone(fact.status)}>{fact.status}</Badge>
                    </td>
                    <td className="mono">{fact.subject}</td>
                    <td className="mono">{fact.predicate}</td>
                    <td>{fact.value}</td>
                    <td className="muted" style={{ fontSize: 12 }}>
                      {(fact.source_refs ?? []).slice(0, 2).join(", ") || "-"}
                    </td>
                    <td>
                      <button
                        className="btn btn-danger"
                        onClick={() => void forget.mutate(fact.fact_id)}
                        disabled={forget.isPending}
                        title={`遗忘 ${fact.fact_id}`}
                      >
                        <Trash2 className="" style={{ width: 13, height: 13 }} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>

        <div
          id="memory-record-form"
          style={{ display: "grid", gap: 16, alignContent: "start" }}
        >
          <Panel title="新增事实" icon={BrainCircuit}>
            <label className="field">
              主题
              <input
                value={recordSubject}
                onChange={(event) => setRecordSubject(event.target.value)}
              />
            </label>
            <label className="field">
              谓词
              <input
                value={recordPredicate}
                onChange={(event) => setRecordPredicate(event.target.value)}
              />
            </label>
            <label className="field">
              值
              <input
                value={recordValue}
                onChange={(event) => setRecordValue(event.target.value)}
                placeholder="观察或验证结果"
              />
            </label>
            <label className="field">
              目标（可选）
              <input
                value={recordTarget}
                onChange={(event) => setRecordTarget(event.target.value)}
                placeholder="http://target"
              />
            </label>
            <label className="field">
              TTL 秒（可选）
              <input
                value={recordExpires}
                onChange={(event) => setRecordExpires(event.target.value)}
                placeholder="86400"
              />
            </label>
            <div className="btn-group" style={{ marginTop: 10 }}>
              <button
                className="btn btn-primary"
                onClick={() => record.mutate()}
                disabled={!recordValue || record.isPending}
                title={!recordValue ? "请先输入值" : undefined}
              >
                {record.isPending ? "保存中..." : "新增"}
              </button>
            </div>
            {record.isError ? (
              <Notice tone="error">{String(record.error)}</Notice>
            ) : null}
          </Panel>

          <Panel title="修正事实" icon={Pencil}>
            <label className="field">
              主题
              <input
                value={fixSubject}
                onChange={(event) => setFixSubject(event.target.value)}
              />
            </label>
            <label className="field">
              谓词
              <input
                value={fixPredicate}
                onChange={(event) => setFixPredicate(event.target.value)}
              />
            </label>
            <label className="field">
              正确值
              <input
                value={fixValue}
                onChange={(event) => setFixValue(event.target.value)}
                placeholder="经验证后的值"
              />
            </label>
            <label className="field">
              原因
              <input
                value={fixReason}
                onChange={(event) => setFixReason(event.target.value)}
              />
            </label>
            <div className="btn-group" style={{ marginTop: 10 }}>
              <button
                className="btn btn-primary"
                onClick={() => fix.mutate()}
                disabled={!fixValue || fix.isPending}
                title={!fixValue ? "请先输入正确值" : undefined}
              >
                {fix.isPending ? "保存中..." : "修正"}
              </button>
            </div>
            {fix.isError ? <Notice tone="error">{String(fix.error)}</Notice> : null}
          </Panel>

          <Panel title="清空记忆" icon={Eraser}>
            <p className="muted" style={{ fontSize: 13 }}>
              清空会以 append-only 方式写入 retract 记录，历史事实仍保留在
              replay 中，但不会再投影给 Agent。
            </p>
            <button
              className="btn btn-danger"
              onClick={() => {
                if (window.confirm("确定清空整个项目记忆？该操作会撤销全部活跃事实。")) {
                  clearAll.mutate();
                }
              }}
              disabled={clearAll.isPending || (snapshot.total_facts ?? 0) === 0}
              title={
                (snapshot.total_facts ?? 0) === 0
                  ? "当前没有可清空的事实"
                  : undefined
              }
            >
              {clearAll.isPending ? "清空中..." : "清空全部"}
            </button>
            {clearAll.isError ? (
              <Notice tone="error">{String(clearAll.error)}</Notice>
            ) : null}
          </Panel>
        </div>
      </div>
    </section>
  );
}
