import { describe, expect, it } from "vitest";

import { layoutGraph } from "../src/graph-layout.js";

describe("graph layout", () => {
  it("lays nodes left to right and builds curved edges", () => {
    const layout = layoutGraph(
      [
        { id: "scanner", status: "succeeded" },
        { id: "verifier", status: "running" },
      ],
      [{ from: "scanner", to: "verifier", label: "facts 2" }],
      900,
      240,
    );

    expect(layout.nodes.map((item) => item.node.id)).toEqual([
      "scanner",
      "verifier",
    ]);
    expect(layout.nodes[0].point.x).toBeLessThan(layout.nodes[1].point.x);
    expect(layout.edges[0].path).toContain("C");
    expect(layout.edges[0].path).not.toContain("NaN");
  });
});
