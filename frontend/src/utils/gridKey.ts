import type { GridSpec } from "../types/api";

/** The key the server gives this grid (services/patch_grid.py GridSpec.key), e.g. 2048x2048_s1024x1024_m40_t0.04. */
export function gridKey(g: GridSpec): string {
  const num = (n: number) => String(Number(n.toPrecision(6))); // Python's :g for the values used here
  const mag = g.target_magnification ? num(g.target_magnification) : "auto";
  return (
    `${g.patch_width}x${g.patch_height}_s${g.stride_x}x${g.stride_y}_m${mag}_t${num(g.min_tissue_fraction)}` +
    (g.include_edge_patches ? "_e" : "") +
    (g.allow_partial_patches ? "_p" : "")
  );
}

/** The grid a key stands for (the inverse of `gridKey`, as GridSpec.from_key on the server), or null. */
export function parseGridKey(key: string): GridSpec | null {
  const m = /^(\d+)x(\d+)_s(\d+)x(\d+)_m([0-9.]+|auto)_t([0-9.]+)(_e)?(_p)?$/.exec(key);
  if (!m) return null;
  return {
    patch_width: Number(m[1]),
    patch_height: Number(m[2]),
    stride_x: Number(m[3]),
    stride_y: Number(m[4]),
    target_magnification: m[5] === "auto" ? null : Number(m[5]),
    min_tissue_fraction: Number(m[6]),
    include_edge_patches: !!m[7],
    allow_partial_patches: !!m[8],
  };
}

/** "2048 px, stride 1024, 40x, tissue >= 4%" */
export function gridLabel(g: GridSpec): string {
  const size = g.patch_width === g.patch_height ? `${g.patch_width}` : `${g.patch_width}x${g.patch_height}`;
  const stride = g.stride_x === g.stride_y ? `${g.stride_x}` : `${g.stride_x}x${g.stride_y}`;
  const mag = g.target_magnification ? `${g.target_magnification}x` : "default magnification";
  const area = g.min_tissue_fraction <= 0 ? "whole slide" : `tissue >= ${Math.round(g.min_tissue_fraction * 100)}%`;
  return `${size} px, stride ${stride}, ${mag}, ${area}`;
}

/** Problems that would make the server refuse the grid, or null. */
export function gridProblem(g: GridSpec): string | null {
  const whole = (n: number) => Number.isInteger(n);
  if (![g.patch_width, g.patch_height].every((n) => whole(n) && n >= 16 && n <= 8192)) return "Patch size must be a whole number from 16 to 8192 px.";
  if (![g.stride_x, g.stride_y].every((n) => whole(n) && n >= 1 && n <= 8192)) return "Stride must be a whole number from 1 to 8192 px.";
  if (!(g.min_tissue_fraction >= 0 && g.min_tissue_fraction <= 1)) return "Minimum tissue must be between 0 and 100%.";
  if (g.target_magnification !== null && !(g.target_magnification > 0 && g.target_magnification <= 200)) return "Magnification must be above 0.";
  return null;
}
