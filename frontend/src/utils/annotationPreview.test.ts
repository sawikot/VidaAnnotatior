import { describe, expect, it } from "vitest";
import { previewFrame, thinPoints, visibleRange } from "./annotationPreview";
import type { Point } from "./coordinates";

describe("previewFrame", () => {
  it("centres a square with a margin around the shape", () => {
    expect(previewFrame("rectangle", [[100, 100], [200, 100], [200, 150], [100, 150]], 1000, 1000)).toEqual({ x: 85, y: 60, side: 130 });
  });

  it("gives a point some surroundings", () => {
    expect(previewFrame("point", [[500, 500]], 1000, 1000)).toEqual({ x: 468, y: 468, side: 64 });
  });

  it("moves the square inside the slide at a corner", () => {
    expect(previewFrame("point", [[2, 998]], 1000, 1000)).toEqual({ x: 0, y: 936, side: 64 });
  });

  it("centres the slide when the shape is wider than it", () => {
    const frame = previewFrame("rectangle", [[0, 0], [100, 0], [100, 40], [0, 40]], 100, 40);
    expect(frame).toEqual({ x: -15, y: -45, side: 130 });
  });
});

describe("thinPoints", () => {
  it("leaves a short outline alone", () => {
    const points: Point[] = [[0, 0], [1, 1]];
    expect(thinPoints(points)).toBe(points);
  });

  it("keeps the ends of a long outline", () => {
    const points = Array.from({ length: 1000 }, (_, i): Point => [i, 0]);
    const thin = thinPoints(points, 10);
    expect(thin).toHaveLength(10);
    expect(thin[0]).toEqual([0, 0]);
    expect(thin[9]).toEqual([999, 0]);
  });
});

describe("visibleRange", () => {
  it("renders only the rows near the view, however long the list", () => {
    expect(visibleRange(6000, 420, 60, 100000)).toEqual({ start: 96, end: 111 });
  });

  it("stops at the ends of the list", () => {
    expect(visibleRange(0, 420, 60, 3)).toEqual({ start: 0, end: 3 });
  });
});
