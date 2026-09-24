import { describe, expect, it } from "vitest";
import type { Point } from "./coordinates";
import { cleanPolygon, polygonClick } from "./polygonDraw";

const tri: Point[] = [
  [0, 0],
  [100, 0],
  [100, 100],
];

describe("clicks while drawing a polygon", () => {
  it("adds a vertex on an ordinary click", () => {
    expect(polygonClick(tri, [0, 100], 1, 5000, { time: 1000, at: [100, 100] })).toBe("add");
    expect(polygonClick([], [5, 5], 1, 0, null)).toBe("add");
  });

  it("closes on a click on the first point, once it has 3", () => {
    expect(polygonClick(tri, [3, 4], 1, 5000, null)).toBe("close");
    expect(polygonClick(tri.slice(0, 2), [3, 4], 1, 5000, null)).toBe("add"); // too few: just a point
  });

  it("the second click of a double-click closes -- even if the mouse moved a few pixels", () => {
    expect(polygonClick(tri, [105, 103], 1, 1200, { time: 1000, at: [100, 100] })).toBe("close");
  });

  it("a double-click with fewer than 3 points adds no stray point", () => {
    expect(polygonClick(tri.slice(0, 2), [101, 1], 1, 1200, { time: 1000, at: [100, 0] })).toBe("ignore");
  });

  it("a slow second click on the last point is an ordinary click", () => {
    expect(polygonClick(tri, [101, 101], 1, 3000, { time: 1000, at: [100, 100] })).toBe("add");
  });

  it("tolerances are screen pixels: zoomed out, a coordinate unit is smaller on screen", () => {
    expect(polygonClick(tri, [30, 30], 10, 5000, null)).toBe("close"); // 42 units = 4.2 screen px at 10 units/px
    expect(polygonClick(tri, [30, 30], 1, 5000, null)).toBe("add");
  });
});

describe("cleaning the polygon to save", () => {
  it("merges near-duplicate points and a last point on top of the first", () => {
    const pts: Point[] = [[0, 0], [100, 0], [100.5, 0.5], [100, 100], [0.4, 0.3]];
    expect(cleanPolygon(pts, 1)).toEqual([[0, 0], [100, 0], [100, 100]]);
  });

  it("leaves a clean polygon alone", () => {
    expect(cleanPolygon(tri, 1)).toEqual(tri);
  });
});
