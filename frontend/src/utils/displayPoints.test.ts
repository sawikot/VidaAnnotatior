import { describe, expect, it } from "vitest";
import { pointsAttr, scaleStep, simplifyPoints } from "./displayPoints";
import type { Point } from "./coordinates";

describe("simplifyPoints", () => {
  const dense: Point[] = Array.from({ length: 1000 }, (_, i) => [i, (i * 7) % 3]);

  it("drops points closer than the tolerance and keeps both ends", () => {
    const out = simplifyPoints(dense, 50);
    expect(out.length).toBeLessThan(30);
    expect(out[0]).toBe(dense[0]);
    expect(out[out.length - 1]).toBe(dense[999]);
  });

  it("leaves short outlines and zero tolerance alone", () => {
    const tri: Point[] = [[0, 0], [1, 0], [0, 1]];
    expect(simplifyPoints(tri, 100)).toBe(tri);
    expect(simplifyPoints(dense, 0)).toBe(dense);
  });

  it("never collapses an outline below three points", () => {
    const tiny: Point[] = [[0, 0], [0.1, 0], [0.2, 0], [0.1, 0.1], [0, 0.1]];
    expect(simplifyPoints(tiny, 10)).toBe(tiny);
  });
});

describe("pointsAttr", () => {
  it("is reused for the same outline and scale", () => {
    const pts: Point[] = Array.from({ length: 200 }, (_, i) => [i, i]);
    expect(pointsAttr(pts, 0.01)).toBe(pointsAttr(pts, 0.01));
    expect(pointsAttr(pts, 100).split(" ")).toHaveLength(200);
  });
});

describe("scaleStep", () => {
  it("rounds nearby scales to the same step", () => {
    expect(scaleStep(0.125)).toBe(scaleStep(0.126));
    expect(scaleStep(0.1) / 0.1).toBeGreaterThan(0.95);
  });
});
