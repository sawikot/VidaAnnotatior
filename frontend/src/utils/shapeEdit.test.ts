import { describe, expect, it } from "vitest";
import type { Point } from "./coordinates";
import {
  dragHandle,
  handlesFor,
  insertVertex,
  isAxisAlignedRect,
  minVertices,
  projectOnSegment,
  removeVertex,
  samePoints,
} from "./shapeEdit";

const W = 100;
const H = 100;
const rect: Point[] = [[20, 20], [60, 20], [60, 50], [20, 50]];
const triangle: Point[] = [[10, 10], [50, 10], [30, 40]];

describe("handlesFor", () => {
  it("gives a polygon or line one handle per vertex, and a point none", () => {
    expect(handlesFor("polygon", triangle)).toHaveLength(3);
    expect(handlesFor("freehand_line", [[0, 0], [5, 5], [9, 0]])).toHaveLength(3);
    expect(handlesFor("line", [[0, 0], [9, 9]])).toHaveLength(2);
    expect(handlesFor("point", [[4, 4]])).toEqual([]);
  });

  it("gives an upright rectangle four corners plus four edge midpoints", () => {
    const spots = handlesFor("rectangle", rect);
    expect(spots).toHaveLength(8);
    expect(spots.filter((s) => s.handle.kind === "edge").map((s) => s.at)).toEqual([[40, 20], [60, 35], [40, 50], [20, 35]]);
  });

  it("falls back to plain vertices for a rectangle that isn't upright", () => {
    const tilted: Point[] = [[20, 20], [60, 25], [60, 50], [20, 50]];
    expect(isAxisAlignedRect(tilted)).toBe(false);
    expect(handlesFor("rectangle", tilted).every((s) => s.handle.kind === "vertex")).toBe(true);
  });

  it("gives a circle a centre and a radius handle at the edge point", () => {
    expect(handlesFor("circle", [[50, 50], [60, 50]]).map((s) => [s.handle.kind, s.at])).toEqual([["center", [50, 50]], ["radius", [60, 50]]]);
  });
});

describe("dragging a vertex", () => {
  it("moves just that vertex and never leaves the patch", () => {
    expect(dragHandle("polygon", triangle, { kind: "vertex", index: 2 }, [35, 45], W, H)).toEqual([[10, 10], [50, 10], [35, 45]]);
    expect(dragHandle("polygon", triangle, { kind: "vertex", index: 0 }, [-20, 500], W, H)[0]).toEqual([0, 100]);
  });

  it("does not modify the original points", () => {
    const copy = triangle.map((p) => [...p]) as Point[];
    dragHandle("polygon", triangle, { kind: "vertex", index: 1 }, [80, 80], W, H);
    expect(triangle).toEqual(copy);
  });
});

describe("resizing a rectangle", () => {
  it("keeps the opposite corner fixed when a corner is dragged", () => {
    expect(dragHandle("rectangle", rect, { kind: "vertex", index: 2 }, [80, 70], W, H)).toEqual([[20, 20], [80, 20], [80, 70], [20, 70]]);
    expect(dragHandle("rectangle", rect, { kind: "vertex", index: 0 }, [10, 5], W, H)).toEqual([[10, 5], [60, 5], [60, 50], [10, 50]]);
  });

  it("stays a valid upright box when a corner is dragged past its opposite", () => {
    const flipped = dragHandle("rectangle", rect, { kind: "vertex", index: 2 }, [5, 5], W, H);
    expect(flipped).toEqual([[5, 5], [20, 5], [20, 20], [5, 20]]);
    expect(isAxisAlignedRect(flipped)).toBe(true);
  });

  it("moves one side when an edge handle is dragged", () => {
    expect(dragHandle("rectangle", rect, { kind: "edge", index: 0 }, [999, 10], W, H)).toEqual([[20, 10], [60, 10], [60, 50], [20, 50]]);
    expect(dragHandle("rectangle", rect, { kind: "edge", index: 1 }, [90, 999], W, H)).toEqual([[20, 20], [90, 20], [90, 50], [20, 50]]);
    expect(dragHandle("rectangle", rect, { kind: "edge", index: 2 }, [0, 80], W, H)).toEqual([[20, 20], [60, 20], [60, 80], [20, 80]]);
    expect(dragHandle("rectangle", rect, { kind: "edge", index: 3 }, [5, 0], W, H)).toEqual([[5, 20], [60, 20], [60, 50], [5, 50]]);
  });

  it("never collapses to a sliver", () => {
    const thin = dragHandle("rectangle", rect, { kind: "edge", index: 1 }, [20, 30], W, H);
    expect(thin[1][0] - thin[0][0]).toBeGreaterThanOrEqual(2);
  });
});

describe("editing a circle", () => {
  const circle: Point[] = [[50, 50], [60, 50]];

  it("changes the radius by dragging the edge handle", () => {
    expect(dragHandle("circle", circle, { kind: "radius" }, [50, 80], W, H)).toEqual([[50, 50], [50, 80]]);
  });

  it("keeps the circle inside the patch", () => {
    const [, edge] = dragHandle("circle", circle, { kind: "radius" }, [500, 50], W, H);
    expect(Math.hypot(edge[0] - 50, edge[1] - 50)).toBeCloseTo(50);
  });

  it("refuses a radius that is practically zero", () => {
    expect(dragHandle("circle", circle, { kind: "radius" }, [51, 50], W, H)).toEqual(circle);
  });

  it("moves the whole circle by its centre handle, within the patch", () => {
    expect(dragHandle("circle", circle, { kind: "center" }, [30, 40], W, H)).toEqual([[30, 40], [40, 40]]);
    const [centre] = dragHandle("circle", circle, { kind: "center" }, [-50, 50], W, H);
    expect(centre[0]).toBeCloseTo(10); // radius 10: cannot go closer to the border than that
  });
});

describe("adding and removing vertices", () => {
  it("projects a point onto a segment", () => {
    expect(projectOnSegment([5, 4], [0, 0], [10, 0])).toEqual({ point: [5, 0], distance: 4 });
    expect(projectOnSegment([-5, 0], [0, 0], [10, 0]).point).toEqual([0, 0]); // clamped to the end
  });

  it("inserts a vertex on the nearest edge of a polygon, keeping the order", () => {
    expect(insertVertex("polygon", triangle, [30, 12], 5)).toEqual([[10, 10], [30, 10], [50, 10], [30, 40]]);
  });

  it("also inserts on the closing edge back to the first point", () => {
    const grown = insertVertex("polygon", triangle, [19, 26], 6)!;
    expect(grown).toHaveLength(4);
    expect(grown[3]).toEqual(expect.arrayContaining([expect.any(Number)]));
    expect(grown.slice(0, 3)).toEqual(triangle);
  });

  it("does nothing when the click is not near the outline", () => {
    expect(insertVertex("polygon", triangle, [30, 25], 3)).toBeNull();
  });

  it("does not close a freehand line", () => {
    const path: Point[] = [[0, 0], [10, 0], [10, 10]];
    expect(insertVertex("freehand_line", path, [5, 5], 2)).toBeNull(); // the closing diagonal doesn't exist
    expect(insertVertex("freehand_line", path, [5, 1], 2)).toEqual([[0, 0], [5, 0], [10, 0], [10, 10]]);
  });

  it("cannot add points to fixed-size shapes", () => {
    expect(insertVertex("rectangle", rect, [40, 20], 5)).toBeNull();
    expect(insertVertex("line", [[0, 0], [10, 0]], [5, 0], 5)).toBeNull();
    expect(insertVertex("circle", [[5, 5], [9, 5]], [9, 5], 5)).toBeNull();
  });

  it("removes a vertex but never below the minimum", () => {
    const square: Point[] = [[0, 0], [10, 0], [10, 10], [0, 10]];
    expect(removeVertex("polygon", square, 1)).toEqual([[0, 0], [10, 10], [0, 10]]);
    expect(removeVertex("polygon", triangle, 0)).toBeNull();
    expect(removeVertex("freehand_line", [[0, 0], [5, 5]], 0)).toBeNull();
    expect(removeVertex("freehand_line", [[0, 0], [5, 5], [9, 0]], 1)).toEqual([[0, 0], [9, 0]]);
    expect(removeVertex("rectangle", rect, 0)).toBeNull();
    expect(removeVertex("polygon", square, 9)).toBeNull();
  });

  it("knows the minimums", () => {
    expect([minVertices("polygon"), minVertices("freehand"), minVertices("freehand_line"), minVertices("line"), minVertices("circle")]).toEqual([3, 3, 2, null, null]);
  });
});

describe("samePoints", () => {
  it("tells an unchanged shape from an edited one", () => {
    expect(samePoints(triangle, triangle.map((p) => [...p] as Point))).toBe(true);
    expect(samePoints(triangle, [[10, 10], [50, 10], [30, 41]])).toBe(false);
    expect(samePoints(triangle, triangle.slice(0, 2))).toBe(false);
  });
});
