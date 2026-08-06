import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  Archive,
  ArchiveRestore,
  ArrowUpRight,
  MessageSquareText,
  Pencil,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { control } from "../api.js";
import { ErrorBanner, Loading } from "../components/Status.js";
import { useRunSelection } from "../store.js";
import { Badge, EmptyState, Kpi, Notice, Panel } from "../components/ui.js";

const ACTIVE_STATUSES = new Set(["requested", "running", "claimed", "paused"]);

export function SessionsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const setSelectedRunId = useRunSelection((state) => state.setSelectedRunId);
  const [showArchived, setShowArchived] = useState(false);
  const [projectFilter, setProjectFilter] = useState("");
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => control.listProjects(),
  });
  const sessions = useQuery({
    queryKey: ["sessions", showArchived, projectFilter],
    queryFn: () =>
      control.requestPublic(
        `/api/v1/sessions?archived=${showArchived}${
          projectFilter ? `&project_id=${encodeURIComponent(projectFilter)}` : ""
        }`,
      ) as unknown as Promise<Array<Record<string, unknown>>>,
    refetchInterval: 4000,
  });
  const updateSession = useMutation({
    mutationFn: (args: {
      id: string;
      title?: string;
      archived?: boolean;
    }) =>
      control.updateSession(args.id, {
        title: args.title,
        archived: args.archived,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
  });
  const deleteSession = useMutation({
    mutationFn: (id: string) => control.deleteSession(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
  });
  const rows = sessions.data ?? [];
  const activeCount = rows.filter((row) =>
    ACTIVE_STATUSES.has(String(row.status ?? "")),
  ).length;
  const archivedCount = rows.filter((row) => row.archived === true).length;

  return (
    <section>
      <header className="page-head">
        <div className="page-head-copy">
          <p className="page-eyebrow">Sessions</p>
          <h1>会话</h1>
          <p className="page-sub">
            管理 Agent 会话：打开控制台、重命名、归档或删除。
          </p>
        </div>
        <div className="actions">
          <button
            className="btn"
            onClick={() => setShowArchived((current) => !current)}
          >
            {showArchived ? "查看未归档" : "查看归档"}
          </button>
          <button className="btn" onClick={() => void sessions.refetch()}>
            <RefreshCw className="" />
            刷新
          </button>
        </div>
      </header>
      <div className="kpi-grid">
        <Kpi label="会话总数" value={rows.length} note="当前视图" />
        <Kpi
          label="活跃会话"
          value={activeCount}
          tone={activeCount ? "info" : undefined}
          note="运行中或暂停"
        />
        <Kpi label="已归档" value={archivedCount} tone="warn" note="仅归档视图可见" />
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
      <Panel title="会话列表" icon={MessageSquareText}>
        {sessions.isLoading ? (
          <Loading label="加载会话" />
        ) : sessions.isError ? (
          <ErrorBanner message={String(sessions.error)} />
        ) : rows.length === 0 ? (
          <EmptyState
            title={showArchived ? "没有归档会话" : "还没有会话"}
            description="启动一次任务后，会话会自动创建在这里。"
            action={
              <button
                className="btn btn-sm"
                onClick={() => navigate("/setup")}
              >
                新建任务
              </button>
            }
          />
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>会话</th>
                  <th>标题</th>
                  <th>运行</th>
                  <th>状态</th>
                  <th>事件</th>
                  <th>最后消息</th>
                  <th>更新时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={String(row.session_id)}>
                    <td className="mono">
                      {String(row.session_id).slice(0, 20)}
                    </td>
                    <td>{String(row.title ?? "")}</td>
                    <td className="mono">{String(row.run_id ?? "").slice(0, 16)}</td>
                    <td>
                      <Badge value={String(row.status ?? "unknown")}>
                        {String(row.status ?? "unknown")}
                      </Badge>
                    </td>
                    <td className="num">{String(row.event_count ?? 0)}</td>
                    <td className="muted">
                      {String(row.last_message ?? "").slice(0, 60) || "-"}
                    </td>
                    <td className="muted">{String(row.updated_at ?? "")}</td>
                    <td>
                      <div className="btn-group">
                        <button
                          className="btn btn-sm"
                          onClick={() => {
                            navigate("/cockpit");
                            setSelectedRunId(String(row.run_id));
                          }}
                        >
                          <ArrowUpRight className="" />
                          打开
                        </button>
                        <button
                          className="btn btn-sm"
                          onClick={() => {
                            const title = window.prompt("会话标题", String(row.title ?? ""));
                            if (title !== null && title.trim()) {
                              updateSession.mutate({
                                id: String(row.session_id),
                                title: title.trim(),
                              });
                            }
                          }}
                        >
                          <Pencil className="" />
                          重命名
                        </button>
                        <button
                          className="btn btn-sm"
                          onClick={() =>
                            updateSession.mutate({
                              id: String(row.session_id),
                              archived: row.archived !== true,
                            })
                          }
                        >
                          {row.archived === true ? (
                            <>
                              <ArchiveRestore className="" />
                              恢复
                            </>
                          ) : (
                            <>
                              <Archive className="" />
                              归档
                            </>
                          )}
                        </button>
                        <button
                          className="btn btn-sm btn-danger"
                          onClick={() => {
                            if (
                              window.confirm(
                                `确定删除会话 ${String(row.session_id).slice(0, 16)}？运行记录仍会保留。`,
                              )
                            ) {
                              deleteSession.mutate(String(row.session_id));
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
        {updateSession.isError ? (
          <Notice tone="error">{String(updateSession.error)}</Notice>
        ) : null}
        {deleteSession.isError ? (
          <Notice tone="error">{String(deleteSession.error)}</Notice>
        ) : null}
      </Panel>
    </section>
  );
}
