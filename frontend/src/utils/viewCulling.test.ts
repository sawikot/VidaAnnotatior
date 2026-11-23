import { describe, expect, it } from "vitest";
import { cullShapes, indexShapes, shapeBox, type CullableShape } from "./viewCulling";

const square = (id: number, x: number, size: number, class_id: number | null = 1): CullableShape => ({
  id,
  type: "rectangle",
  points: [[x, 0], [x + size, 0], [x + size, size], [x, size]],
  class_id,
});
const view = { x0: 0, y0: 0, x1: 100, y1: 100 };
const ids = (list: CullableShape[]) => list.map((s) => s.id);

describe("cullShapes", () => {
  it("skips shapes outside the view", () => {
    const out = cullShapes(indexShapes([square(1, 10, 5), square(2, 500, 5)]), view, 1, null);
    expect(ids(out.detailed)).toEqual([1]);
    expect(out.markerCount).toBe(0);
  });

  it("keeps a shape that only crosses into the view", () => {
    expect(ids(cullShapes(indexShapes([square(1, 90, 50)]), view, 1, null).detailed)).toEqual([1]);
  });

  it("keeps the selected shape even when it is out of view", () => {
    expect(ids(cullShapes(indexShapes([square(1, 10, 5), square(2, 500, 5)]), view, 1, 2).detailed).sort()).toEqual([1, 2]);
  });

  it("over the cap, draws the largest and leaves none of the others out", () => {
    const shapes = [square(1, 0, 1), square(2, 10, 30), square(3, 50, 2), square(4, 60, 20)];
    const out = cullShapes(indexShapes(shapes), view, 1, null, 2);
    expect(ids(out.detailed).sort()).toEqual([2, 4]);
    expect(out.markerCount).toBe(2);
    expect(out.markers[0].d.match(/M/g)).toHaveLength(2);
  });

  it("respects the cap when every shape is the same size", () => {
    const shapes = Array.from({ length: 10 }, (_, i) => square(i + 1, i, 1));
    const out = cullShapes(indexShapes(shapes), view, 1, null, 3);
    expect(out.detailed).toHaveLength(3);
    expect(out.markerCount).toBe(7);
  });

  it("measures an outline too long to spread into Math.min", () => {
    const points = Array.from({ length: 300_000 }, (_, i): [number, number] => [i, 1]);
    expect(shapeBox("freehand", points).x1).toBe(299_999);
  });
});
