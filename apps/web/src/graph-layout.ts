export interface GraphNodeInput {
  id: string;
  status: string;
  detail?: string;
}

export interface GraphEdgeInput {
  from: string;
  to: string;
  label?: string;
}

export interface GraphPoint {
  x: number;
  y: number;
}

export interface LayoutNode {
  node: GraphNodeInput;
  point: GraphPoint;
  index: number;
}

export interface LayoutEdge {
  edge: GraphEdgeInput;
  from: GraphPoint;
  to: GraphPoint;
  path: string;
}

export interface GraphLayout {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
}

export function layoutGraph(
  nodes: GraphNodeInput[],
  edges: GraphEdgeInput[],
  width: number,
  height: number,
): GraphLayout {
  const count = Math.max(nodes.length, 1);
  const step = count > 1 ? (width - 220) / (count - 1) : 0;
  const y = height / 2;
  const points = new Map<string, GraphPoint>();
  const laidOut: LayoutNode[] = nodes.map((node, index) => {
    const point = { x: 110 + index * step, y };
    points.set(node.id, point);
    return { node, point, index };
  });
  const laidOutEdges: LayoutEdge[] = edges
    .filter((edge) => points.has(edge.from) && points.has(edge.to))
    .map((edge) => {
      const from = points.get(edge.from)!;
      const to = points.get(edge.to)!;
      const dx = to.x - from.x;
      const curve = Math.min(48, Math.max(24, Math.abs(dx) * 0.35));
      const path =
        `M ${from.x} ${from.y} ` +
        `C ${from.x + dx * 0.25} ${from.y - curve}, ` +
        `${to.x - dx * 0.25} ${to.y - curve}, ${to.x} ${to.y}`;
      return { edge, from, to, path };
    });
  return { nodes: laidOut, edges: laidOutEdges };
}
