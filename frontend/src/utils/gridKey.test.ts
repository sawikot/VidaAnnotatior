import { describe, expect, it } from "vitest";
import { gridKey, gridLabel, parseGridKey, gridProblem } from "./gridKey";

const base = { patch_width: 2048, patch_height: 2048, stride_x: 1024, stride_y: 1024, target_magnification: 40, min_tissue_fraction: 0.04, include_edge_patches: false, allow_partial_patches: false };

describe("grid keys", () => {
  it("matches the server's key format", () => {
    expect(gridKey(base)).toBe("2048x2048_s1024x1024_m40_t0.04");
    expect(gridKey({ ...base, patch_height: 512, target_magnification: null, min_tissue_fraction: 0.5, include_edge_patches: true })).toBe(
      "2048x512_s1024x1024_mauto_t0.5_e",
    );
    expect(gridKey({ ...base, target_magnification: 20.5, min_tissue_fraction: 0 })).toBe("2048x2048_s1024x1024_m20.5_t0");
  });

  it("reads well", () => {
    expect(gridLabel(base)).toBe("2048 px, stride 1024, 40x, tissue >= 4%");
    expect(gridLabel({ ...base, min_tissue_fraction: 0 })).toBe("2048 px, stride 1024, 40x, whole slide");
  });

  it("flags grids the server would refuse", () => {
    expect(gridProblem(base)).toBeNull();
    expect(gridProblem({ ...base, patch_width: 8 })).toMatch(/Patch size/);
    expect(gridProblem({ ...base, stride_x: 0 })).toMatch(/Stride/);
    expect(gridProblem({ ...base, min_tissue_fraction: 2 })).toMatch(/tissue/);
  });
});

describe("parseGridKey", () => {
  it("reads back every key it makes, and nothing else", () => {
    const g = { patch_width: 2048, patch_height: 1024, stride_x: 1024, stride_y: 512, target_magnification: 40, min_tissue_fraction: 0.6, include_edge_patches: true, allow_partial_patches: false };
    expect(parseGridKey(gridKey(g))).toEqual(g);
    expect(parseGridKey(gridKey({ ...g, target_magnification: null, min_tissue_fraction: 0 }))).toMatchObject({ target_magnification: null, min_tissue_fraction: 0 });
    expect(parseGridKey("image")).toBeNull();
  });
});
