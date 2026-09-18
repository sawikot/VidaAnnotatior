import { describe, expect, it } from "vitest";
import {
  bestLevelForMagnification,
  level0BoundsOfPatch,
  level0ToPatchLocal,
  patchFootprintL0,
  patchLocalToLevel0,
  polygonLevel0ToPatchLocal,
  polygonPatchLocalToLevel0,
  type PatchOrigin,
} from "./coordinates";

describe("coordinate transform", () => {
  it("matches the spec's worked example at same resolution", () => {
    // origin (20000, 15000), local (100, 80), downsample=1 -> global (20100, 15080)
    const origin: PatchOrigin = { x: 20000, y: 15000, level: 0, downsample: 1.0 };
    expect(patchLocalToLevel0(origin, 100, 80)).toEqual([20100, 15080]);
  });

  it("scales the local offset for a downsampled patch", () => {
    const origin: PatchOrigin = { x: 20000, y: 15000, level: 1, downsample: 2.0 };
    expect(patchLocalToLevel0(origin, 100, 80)).toEqual([20200, 15160]);
  });

  it("round-trips local -> global -> local", () => {
    const origin: PatchOrigin = { x: 48213, y: 91007, level: 2, downsample: 4.0 };
    for (const [lx, ly] of [
      [0, 0],
      [511, 511],
      [37.5, 402.2],
    ] as const) {
      const [gx, gy] = patchLocalToLevel0(origin, lx, ly);
      const [bx, by] = level0ToPatchLocal(origin, gx, gy);
      expect(bx).toBeCloseTo(lx, 9);
      expect(by).toBeCloseTo(ly, 9);
    }
  });

  it("transforms a polygon pointwise, consistently", () => {
    const origin: PatchOrigin = { x: 1000, y: 2000, level: 0, downsample: 1.5 };
    const points: [number, number][] = [
      [10, 20],
      [100, 200],
      [0, 0],
    ];
    const polyL0 = polygonPatchLocalToLevel0(origin, points);
    const expected = points.map(([x, y]) => patchLocalToLevel0(origin, x, y));
    expect(polyL0).toEqual(expected);

    const back = polygonLevel0ToPatchLocal(origin, polyL0);
    back.forEach(([bx, by], i) => {
      expect(bx).toBeCloseTo(points[i][0], 9);
      expect(by).toBeCloseTo(points[i][1], 9);
    });
  });

  it("computes patch footprint and bounds in Level-0 pixels", () => {
    expect(patchFootprintL0(512, 512, 2.0)).toEqual([1024, 1024]);

    const origin: PatchOrigin = { x: 20000, y: 15000, level: 1, downsample: 2.0 };
    expect(level0BoundsOfPatch(origin, 512, 512)).toEqual([20000, 15000, 21024, 16024]);
  });

  it("picks the pyramid level closest to the requested magnification", () => {
    // 40x native, levels at downsample 1,2,4,8 -> effective mag 40,20,10,5x
    expect(bestLevelForMagnification(40, [1, 2, 4, 8], 20)).toBe(1);
    expect(bestLevelForMagnification(40, [1, 2, 4, 8], 40)).toBe(0);
  });
});
