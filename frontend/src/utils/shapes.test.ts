import { describe, expect, it } from "vitest";
import type { Point } from "./coordinates";
import {
  circleGeometry,
  clampTranslation,
  constrainCircleEdge,
  isAreaShape,
  isDrawnEnough,
  isLineShape,
  lineLength,
  shapeArea,
  shapeBounds,
  translatePoints,
} from "./shapes";

describe("shape kinds", () => {
  it("separates area shapes, lines and points", () => {
    expect(["polygon", "freehand", "rectangle", "circle"].every((t) => isAreaShape(t as never))).toBe(true);
    expect(["line", "freehand_line"].every((t) => isLineShape(t as never))).toBe(true);
    expect(isAreaShape("point") || isLineShape("point")).toBe(false);
    expect(isAreaShape("line") || isLineShape("circle")).toBe(false);
  });
});

describe("circle and line measurements", () => {
  it("reads a circle as centre plus radius", () => {
    expect(circleGeometry([[10, 20], [13, 24]])).toEqual({ cx: 10, cy: 20, r: 5 });
  });

  it("measures a line along its whole path", () => {
    expect(lineLength([[0, 0], [3, 4]])).toBe(5);
    expect(lineLength([[0, 0], [3, 4], [3, 10]])).toBe(11);
    expect(lineLength([[1, 1]])).toBe(0);
  });
});

describe("shapeBounds", () => {
  it("boxes a circle by its radius, not by its two points", () => {
    expect(shapeBounds("circle", [[50, 50], [60, 50]])).toEqual({ minX: 40, minY: 40, maxX: 60, maxY: 60 });
  });

  it("boxes any other shape by its points", () => {
    expect(shapeBounds("line", [[5, 9], [2, 4]])).toEqual({ minX: 2, minY: 4, maxX: 5, maxY: 9 });
  });
});

describe("moving a shape", () => {
  const square: Point[] = [[10, 10], [30, 10], [30, 30], [10, 30]];

  it("translates every point", () => {
    expect(translatePoints(square, 5, -3)).toEqual([[15, 7], [35, 7], [35, 27], [15, 27]]);
  });

  it("stops at the patch edges instead of leaving it", () => {
    expect(clampTranslation("rectangle", square, -50, 0, 100, 100)).toEqual([-10, 0]);
    expect(clampTranslation("rectangle", square, 500, 500, 100, 100)).toEqual([70, 70]);
    expect(clampTranslation("rectangle", square, 5, 5, 100, 100)).toEqual([5, 5]);
  });

  it("accounts for a circle's radius when clamping", () => {
    const circle: Point[] = [[50, 50], [60, 50]]; // radius 10
    expect(clampTranslation("circle", circle, -100, 0, 100, 100)).toEqual([-40, 0]);
    expect(clampTranslation("circle", circle, 100, 0, 100, 100)).toEqual([40, 0]);
  });

  it("does not move a shape that is larger than the patch", () => {
    const huge: Point[] = [[-5, -5], [200, 200]];
    expect(clampTranslation("line", huge, 30, 30, 100, 100)).toEqual([0, 0]);
  });
});

describe("constrainCircleEdge", () => {
  it("leaves an edge alone when the circle fits", () => {
    expect(constrainCircleEdge([50, 50], [70, 50], 100, 100)).toEqual([70, 50]);
  });

  it("shortens the radius, keeping the direction, so the circle stays inside the patch", () => {
    const [x, y] = constrainCircleEdge([20, 50], [120, 50], 100, 100); // free space to the left edge is 20
    expect(x).toBeCloseTo(40);
    expect(y).toBeCloseTo(50);
    const diagonal = constrainCircleEdge([10, 10], [60, 60], 100, 100);
    expect(Math.hypot(diagonal[0] - 10, diagonal[1] - 10)).toBeCloseTo(10);
  });

  it("handles a zero-length drag", () => {
    expect(constrainCircleEdge([5, 5], [5, 5], 100, 100)).toEqual([5, 5]);
  });
});

describe("isDrawnEnough", () => {
  it("ignores stray clicks and tiny drags", () => {
    expect(isDrawnEnough("line", [[0, 0], [2, 1]])).toBe(false);
    expect(isDrawnEnough("line", [[0, 0], [20, 0]])).toBe(true);
    expect(isDrawnEnough("circle", [[10, 10], [11, 10]])).toBe(false);
    expect(isDrawnEnough("circle", [[10, 10], [20, 10]])).toBe(true);
    expect(isDrawnEnough("rectangle", [[0, 0], [30, 0], [30, 2], [0, 2]])).toBe(false);
    expect(isDrawnEnough("rectangle", [[0, 0], [30, 0], [30, 20], [0, 20]])).toBe(true);
    expect(isDrawnEnough("freehand_line", [[0, 0], [3, 0]])).toBe(false);
    expect(isDrawnEnough("freehand_line", [[0, 0], [5, 0], [10, 5]])).toBe(true);
  });

  it("needs three points for a closed outline", () => {
    expect(isDrawnEnough("polygon", [[0, 0], [1, 1]])).toBe(false);
    expect(isDrawnEnough("freehand", [[0, 0], [1, 1], [2, 0]])).toBe(true);
    expect(isDrawnEnough("point", [[4, 4]])).toBe(true);
  });
});

describe("shapeArea", () => {
  it("is exact for a circle and the enclosed area for polygons", () => {
    expect(shapeArea("circle", [[0, 0], [3, 4]])).toBeCloseTo(Math.PI * 25);
    expect(shapeArea("rectangle", [[0, 0], [10, 0], [10, 5], [0, 5]])).toBe(50);
    expect(shapeArea("polygon", [[0, 0], [10, 0], [0, 10]])).toBe(50);
  });

  it("is zero for points and lines", () => {
    expect(shapeArea("point", [[3, 3]])).toBe(0);
    expect(shapeArea("line", [[0, 0], [9, 9]])).toBe(0);
    expect(shapeArea("freehand_line", [[0, 0], [5, 5], [9, 0]])).toBe(0);
  });
});
