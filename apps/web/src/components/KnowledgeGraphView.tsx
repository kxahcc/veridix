import { useEffect, useRef, useState } from "react";
import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import { Network } from "lucide-react";
import { EmptyState, Notice, Panel } from "./ui.js";

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

const NODE_COLORS: Record<string, string> = {
  technique: "#4f7bd9",
  playbook: "#2f9e7f",
  oracle: "#9d6bb5",
  concept: "#d59a3a",
  cwe: "#d65b5b",
  entity: "#5c7cfa",
  mitre: "#b7791f",
  tool: "#4aa3c2",
};

function nodeColor(type: string): string {
  return NODE_COLORS[type] ?? "#6b7280";
}

export function KnowledgeGraphView({
  graph,
}: {
  graph: KnowledgeGraph | undefined;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const nodes = graph?.nodes ?? [];
  const edges = graph?.edges ?? [];
  const nodeIds = new Set(nodes.map((node) => node.id));
  const visibleEdges = edges.filter(
    (edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target),
  );

  useEffect(() => {
    const container = containerRef.current;
    const nodes = graph?.nodes ?? [];
    const edges = graph?.edges ?? [];
    const nodeIds = new Set(nodes.map((node) => node.id));
    const visibleEdges = edges.filter(
      (edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target),
    );
    if (!container || nodes.length === 0) {
      return;
    }
    const elements: ElementDefinition[] = [
      ...nodes.map((node) => ({
        data: {
          id: node.id,
          label: node.label,
          nodeType: node.type,
          chunks: node.chunks,
        },
      })),
      ...visibleEdges.map((edge) => ({
        data: {
          id: `${edge.source}->${edge.target}->${edge.predicate}`,
          source: edge.source,
          target: edge.target,
          label: edge.predicate,
        },
      })),
    ];
    let cy: Core | null = null;
    try {
      cy = cytoscape({
        container,
        elements,
        minZoom: 0.25,
        maxZoom: 3,
        wheelSensitivity: 0.22,
        style: [
          {
            selector: "node",
            style: {
              "background-color": (element) =>
                nodeColor(String(element.data("nodeType") ?? "")),
              label: "data(label)",
              color: "#dbe7f7",
              "font-size": "10",
              "text-wrap": "wrap",
              "text-max-width": "110",
              width: "30",
              height: "30",
              "border-width": 1,
              "border-color": "rgba(255,255,255,0.22)",
            },
          },
          {
            selector: "edge",
            style: {
              width: "1",
              "line-color": "rgba(110, 160, 225, 0.42)",
              "target-arrow-color": "rgba(160, 200, 245, 0.55)",
              "target-arrow-shape": "triangle",
              "curve-style": "bezier",
              label: "data(label)",
              "font-size": "8",
              color: "rgba(170, 190, 220, 0.75)",
              "text-background-color": "#111827",
              "text-background-opacity": 0.7,
              "text-background-padding": "2",
            },
          },
          {
            selector: ":selected",
            style: {
              "border-width": 3,
              "border-color": "#fbbf24",
              "overlay-opacity": 0,
            },
          },
        ],
        layout: {
          name: "cose",
          animate: false,
          padding: 28,
          nodeRepulsion: 3200,
          idealEdgeLength: 100,
          edgeElasticity: 120,
          gravity: 0.12,
        },
      });
      cy.on("tap", "node", (event) => {
        const id = event.target.id();
        setSelected(nodes.find((node) => node.id === id) ?? null);
      });
      cy.on("tap", (event) => {
        if (event.target === cy) {
          setSelected(null);
        }
      });
    } catch (error) {
      console.error("knowledge graph render failed", error);
    }
    return () => {
      cy?.destroy();
    };
  }, [graph]);

  if (!graph) {
    return null;
  }
  if (
    graph.counts.nodes > nodes.length ||
    graph.counts.edges > visibleEdges.length
  ) {
    return (
      <Panel title="知识图谱" icon={Network}>
        <Notice tone="info">
          图谱共 {graph.counts.nodes} 节点 / {graph.counts.edges} 边，当前展示
          最相关 {nodes.length} 节点 / {visibleEdges.length} 边。
        </Notice>
      </Panel>
    );
  }
  if (nodes.length === 0) {
    return (
      <Panel title="知识图谱" icon={Network}>
        <EmptyState
          title="暂无图谱数据"
          description="知识分块带 graph frontmatter 或由内置来源注册后生成。"
        />
      </Panel>
    );
  }
  return (
    <Panel
      title="知识图谱"
      icon={Network}
      actions={
        <span className="muted" style={{ fontSize: 12 }}>
          {nodes.length} 节点 · {visibleEdges.length} 边 · 可拖拽 / 缩放
        </span>
      }
    >
      <div
        ref={containerRef}
        className="knowledge-graph-canvas"
        role="img"
        aria-label="interactive knowledge graph"
      />
      {selected ? (
        <div className="graph-node-detail">
          <strong>{selected.label}</strong>
          <span className="muted">
            {selected.type} · {selected.chunks} 个知识分块
          </span>
        </div>
      ) : (
        <p className="muted" style={{ fontSize: 12, margin: "8px 0 0" }}>
          点击节点查看详情；拖动画布平移，滚轮缩放。
        </p>
      )}
    </Panel>
  );
}
