import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { control } from "../api.js";
import { ErrorBanner, Loading } from "../components/Status.js";
import { Badge, EmptyState, Notice } from "../components/ui.js";
import {
  layoutGraph,
  type GraphEdgeInput,
  type GraphNodeInput,
} from "../graph-layout.js";

type AttackNode = {
  id: string;
  label: string;
  kind: string;
};

type AttackEdge = {
  source: string;
  target: string;
  predicate: string;
};

const KIND_COLORS: Record<string, string> = {
  target: "#0d9488",
  endpoint: "#2563eb",
  vulnerability: "#c0392b",
};

export function AttackGraphPanel({ runId }: { runId: string }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const graph = useQuery({
    queryKey: ["attack-graph", runId],
    queryFn: () =>
      control.requestPublic(`/api/v1/runs/${runId}/attack-graph`),
    enabled: Boolean(runId),
    refetchInterval: 5000,
  });
  const payload = graph.data as
    | { nodes?: AttackNode[]; edges?: AttackEdge[] }
    | undefined;
  const analysis = useMemo(() => {
    if (!selectedId) {
      return null;
    }
    const source = payload?.nodes?.find((node) => node.id === selectedId);
    const depthMap = new Map<string, number>([[selectedId, 0]]);
    const parents = new Map<string, string>();
    const queue = [selectedId];
    while (queue.length) {
      const current = queue.shift()!;
      const depth = depthMap.get(current) ?? 0;
      if (depth >= 3) {
        continue;
      }
      for (const edge of payload?.edges ?? []) {
        if (edge.source !== current || depthMap.has(edge.target)) {
          continue;
        }
        depthMap.set(edge.target, depth + 1);
        parents.set(edge.target, current);
        queue.push(edge.target);
      }
    }
    const highlighted = new Set<string>();
    for (const edge of payload?.edges ?? []) {
      if (
        parents.get(edge.target) === edge.source &&
        depthMap.has(edge.target)
      ) {
        highlighted.add(`${edge.source}->${edge.target}`);
      }
    }
    const labelOf = (id: string) =>
      payload?.nodes?.find((node) => node.id === id)?.label ?? id;
    const paths: string[][] = [];
    for (const target of depthMap.keys()) {
      if (target === selectedId) {
        continue;
      }
      const chain: string[] = [];
      let cursor: string = target;
      let guard = 0;
      while (cursor && cursor !== selectedId && guard < 10) {
        chain.unshift(cursor);
        cursor = parents.get(cursor) ?? "";
        guard += 1;
      }
      if (cursor === selectedId) {
        chain.unshift(selectedId);
        paths.push(chain.map(labelOf));
      }
    }
    return {
      source,
      depthCount: depthMap.size,
      depthMap,
      highlighted,
      paths: paths.slice(0, 8),
    };
  }, [payload, selectedId]);
  if (graph.isLoading) {
    return <Loading label="Loading attack graph" />;
  }
  if (graph.isError) {
    return <ErrorBanner message={String(graph.error)} />;
  }
  const nodes: GraphNodeInput[] = (payload?.nodes ?? []).map((node) => ({
    id: node.id,
    status: node.kind,
    detail: node.label,
  }));
  const edges: GraphEdgeInput[] = (payload?.edges ?? []).map((edge) => ({
    from: edge.source,
    to: edge.target,
    label: edge.predicate,
  }));
  const layout = layoutGraph(nodes, edges, 860, 260);

  const selectedNode = selectedId
    ? payload?.nodes?.find((node) => node.id === selectedId)
    : null;

  return (
    <div>
      <div className="stack" style={{ gap: 6, marginBottom: 8 }}>
        <Notice tone="info">
          {nodes.length} 个节点 · {edges.length} 条边 ·{" "}
          点击节点查看可达攻击路径
        </Notice>
        <div className="stack" style={{ flexDirection: "row", gap: 6 }}>
          <Badge value="target">target</Badge>
          <Badge value="endpoint">endpoint</Badge>
          <Badge value="vulnerability">vulnerability</Badge>
        </div>
      </div>
      {nodes.length === 0 ? (
        <EmptyState
          title="暂无攻击图节点"
          description="该运行还没有 findings；Agent 产生验证过的发现后会自动补全节点与路径。"
        />
      ) : (
        <svg
          className="graph-canvas"
          viewBox="0 0 860 260"
          role="img"
          aria-label="攻击图"
        >
        {layout.edges.map((edge, index) => {
          const key = `${edge.edge.from}->${edge.edge.to}`;
          const highlight = analysis?.highlighted.has(key) ?? false;
          return (
            <g key={index}>
              <path
                d={edge.path}
                fill="none"
                className={highlight ? "graph-edge highlight" : "graph-edge"}
                stroke={highlight ? "#22d3ee" : "#8aa0ad"}
                strokeWidth={highlight ? 2.5 : 1.5}
              />
              {edge.edge.label && (
                <text
                  x={(edge.from.x + edge.to.x) / 2}
                  y={Math.min(edge.from.y, edge.to.y) - 14}
                  textAnchor="middle"
                  fontSize="11"
                  fill="#8ea3b5"
                >
                  {edge.edge.label}
                </text>
              )}
            </g>
          );
        })}
        {layout.nodes.map((item) => {
          const id = item.node.id;
          const isSelected = id === selectedId;
          const isReachable = analysis?.depthMap.has(id) ?? false;
          return (
            <g
              key={id}
              className={`graph-node${isSelected ? " selected" : ""}${
                isReachable && !isSelected ? " reachable" : ""
              }`}
              role="button"
              tabIndex={0}
              aria-label={`节点 ${String(item.node.detail ?? id)}`}
              onClick={() => setSelectedId(isSelected ? null : id)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  setSelectedId(isSelected ? null : id);
                }
              }}
            >
              <circle
                cx={item.point.x}
                cy={item.point.y}
                r="8"
                fill={KIND_COLORS[item.node.status] ?? "#8a5a00"}
              />
              <text
                x={item.point.x + 14}
                y={item.point.y + 4}
                fontSize="12"
                fill="#e8eef4"
              >
                {String(item.node.detail ?? item.node.id).slice(0, 36)}
              </text>
            </g>
          );
        })}
        </svg>
      )}
      <div className="graph-inspector" aria-live="polite">
        {analysis && selectedNode ? (
          <>
            <div className="panel-head">
              <div className="card-title">攻击路径分析</div>
              <Badge value={selectedNode.kind}>{selectedNode.kind}</Badge>
            </div>
            <p className="muted">
              {selectedNode.label} · 可达 {analysis.depthCount} 节点
            </p>
            {analysis.paths.length ? (
              <ul className="graph-paths">
                {analysis.paths.map((path, index) => (
                  <li key={index} className="graph-path">
                    {path.join(" -> ")}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">没有向外的攻击路径。</p>
            )}
            <button
              className="btn btn-sm"
              onClick={() => setSelectedId(null)}
            >
              清除选择
            </button>
          </>
        ) : (
          <p className="muted">点击节点查看其可达攻击路径。</p>
        )}
      </div>
    </div>
  );
}
