import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { control } from "../api.js";
import { useRunSelection } from "../store.js";

type RunRow = {
  run_id: string;
  mission_id: string;
  status: string;
  created_at?: string;
};

export function RunPicker() {
  const selectedRunId = useRunSelection((state) => state.selectedRunId);
  const setSelectedRunId = useRunSelection(
    (state) => state.setSelectedRunId,
  );
  const navigate = useNavigate();
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: () => control.requestPublic("/api/v1/runs"),
  });
  const rows = ((runs.data ?? []) as RunRow[]).slice(0, 30);

  if (runs.isLoading) {
    return (
      <div className="run-picker">
        <span className="muted">Loading runs...</span>
      </div>
    );
  }
  if (rows.length === 0) {
    return (
      <div className="run-picker">
        <span className="muted">暂无运行。</span>
        <button className="btn btn-sm" onClick={() => navigate("/setup")}>
          新建任务
        </button>
      </div>
    );
  }
  return (
    <div className="run-picker">
      <select
        aria-label="选择运行"
        value={selectedRunId ?? ""}
        onChange={(event) => setSelectedRunId(event.target.value || null)}
      >
        <option value="">选择运行...</option>
        {rows.map((run) => (
          <option key={run.run_id} value={run.run_id}>
            {run.run_id.slice(0, 18)} · {run.status}
            {run.created_at ? ` · ${run.created_at.slice(0, 10)}` : ""}
          </option>
        ))}
      </select>
      <button
        className="btn btn-sm"
        onClick={() => void runs.refetch()}
        title="刷新运行列表"
      >
        <RefreshCw className="" />
      </button>
    </div>
  );
}
