import type { ClassSyncItem } from "../../services/api";
import type { ConfigVersion } from "../../types/api";

/** Fields that define the patch grid (GRID_FIELDS in backend/app/services/config_versioning.py), restricted
 * to the ones the edit form exposes. Changing them never moves existing patches: each keeps its own grid. */
export const CRITICAL_FIELDS = [
  "patch_width",
  "patch_height",
  "stride_x",
  "stride_y",
  "target_magnification",
  "min_tissue_fraction",
] as const;

export const CRITICAL_LABELS: Record<string, string> = {
  patch_width: "Patch width",
  patch_height: "Patch height",
  stride_x: "Stride X",
  stride_y: "Stride Y",
  target_magnification: "Target magnification",
  min_tissue_fraction: "Minimum tissue fraction",
};

export interface ClassDraft {
  id?: number;
  name: string;
  color_hex: string;
  /** Class ID as typed (digits); blank for none. */
  code?: string;
}

export interface ConfigDraft {
  title: string;
  target_magnification: number;
  mpp_handling: string;
  patch_width: number;
  patch_height: number;
  stride_x: number;
  stride_y: number;
  min_tissue_pct: number;
  allow_partial_patches: boolean;
  include_edge_patches: boolean;
  otsu_sensitivity: number;
  morph_open_px: number;
  morph_close_px: number;
  min_component_px: number;
  enabled_tools: string[];
  allow_skip: boolean;
  allow_unsure: boolean;
  require_annotation: boolean;
  reviewer_mode: boolean;
  classes: ClassDraft[];
}

export const TISSUE_DEFAULTS = { otsu_sensitivity: 0.65, morph_open_px: 3, morph_close_px: 5, min_component_px: 400 };

export function tissueParamsOf(config: ConfigVersion) {
  const p = (config.tissue_params ?? {}) as Partial<typeof TISSUE_DEFAULTS>;
  return {
    otsu_sensitivity: p.otsu_sensitivity ?? TISSUE_DEFAULTS.otsu_sensitivity,
    morph_open_px: p.morph_open_px ?? TISSUE_DEFAULTS.morph_open_px,
    morph_close_px: p.morph_close_px ?? TISSUE_DEFAULTS.morph_close_px,
    min_component_px: p.min_component_px ?? TISSUE_DEFAULTS.min_component_px,
  };
}

export function draftFromConfig(c: ConfigVersion): ConfigDraft {
  return {
    title: c.title ?? "",
    target_magnification: c.target_magnification ?? 20,
    mpp_handling: c.mpp_handling,
    patch_width: c.patch_width,
    patch_height: c.patch_height,
    stride_x: c.stride_x,
    stride_y: c.stride_y,
    min_tissue_pct: Math.round(c.min_tissue_fraction * 100),
    allow_partial_patches: c.allow_partial_patches,
    include_edge_patches: c.include_edge_patches,
    ...tissueParamsOf(c),
    enabled_tools: [...c.enabled_tools],
    allow_skip: c.allow_skip,
    allow_unsure: c.allow_unsure,
    require_annotation: c.require_annotation,
    reviewer_mode: c.reviewer_mode,
    classes: c.annotation_classes.map((k) => ({
      id: k.id,
      name: k.name,
      color_hex: k.color_hex,
      code: k.code == null ? "" : String(k.code),
    })),
  };
}

export function toClassSync(classes: ClassDraft[]): ClassSyncItem[] {
  return classes.map((k) => ({
    id: k.id,
    name: k.name.trim(),
    color_hex: k.color_hex,
    code: k.code?.trim() ? Number(k.code.trim()) : null,
  }));
}

export interface DraftDiff {
  /** Only the config fields that differ from the original (API field names). */
  fields: Record<string, unknown>;
  /** Full replacement class list, or null when classes are unchanged. */
  classes: ClassSyncItem[] | null;
  /** Human labels of changed geometry fields. */
  touchedCritical: string[];
  changed: boolean;
}

export function diffDraft(draft: ConfigDraft, original: ConfigVersion): DraftDiff {
  const fields: Record<string, unknown> = {};

  if (draft.title.trim() !== (original.title ?? "")) fields.title = draft.title.trim() || null;
  if (draft.target_magnification !== (original.target_magnification ?? 20)) {
    fields.target_magnification = draft.target_magnification;
  }
  if (draft.mpp_handling !== original.mpp_handling) fields.mpp_handling = draft.mpp_handling;
  for (const key of ["patch_width", "patch_height", "stride_x", "stride_y"] as const) {
    if (draft[key] !== original[key]) fields[key] = draft[key];
  }
  const fraction = draft.min_tissue_pct / 100;
  if (Math.abs(fraction - original.min_tissue_fraction) > 1e-9) fields.min_tissue_fraction = fraction;
  for (const key of ["allow_partial_patches", "include_edge_patches", "allow_skip", "allow_unsure", "require_annotation", "reviewer_mode"] as const) {
    if (draft[key] !== original[key]) fields[key] = draft[key];
  }
  if (draft.enabled_tools.join(",") !== original.enabled_tools.join(",")) fields.enabled_tools = draft.enabled_tools;

  const tissueNow = tissueParamsOf(original);
  const tissueDraft = {
    otsu_sensitivity: draft.otsu_sensitivity,
    morph_open_px: draft.morph_open_px,
    morph_close_px: draft.morph_close_px,
    min_component_px: draft.min_component_px,
  };
  if (JSON.stringify(tissueNow) !== JSON.stringify(tissueDraft)) {
    fields.tissue_params = { ...original.tissue_params, ...tissueDraft };
  }

  const before = JSON.stringify(toClassSync(draftFromConfig(original).classes));
  const nextClasses = toClassSync(draft.classes);
  const classes = JSON.stringify(nextClasses) === before ? null : nextClasses;

  const touchedCritical = CRITICAL_FIELDS.filter((f) => f in fields).map((f) => CRITICAL_LABELS[f]);
  return { fields, classes, touchedCritical, changed: Object.keys(fields).length > 0 || classes !== null };
}

/** First problem found, or null. The server validates too; this just avoids a
 * round trip for the obvious mistakes. */
export function validateDraft(d: ConfigDraft): string | null {
  const intIn = (v: number, lo: number, hi: number) => Number.isInteger(v) && v >= lo && v <= hi;
  if (!intIn(d.patch_width, 16, 8192) || !intIn(d.patch_height, 16, 8192)) return "Patch width/height must be whole numbers from 16 to 8192.";
  if (!intIn(d.stride_x, 1, 8192) || !intIn(d.stride_y, 1, 8192)) return "Stride must be a whole number from 1 to 8192.";
  if (!(d.target_magnification > 0 && d.target_magnification <= 200)) return "Target magnification must be between 0 and 200.";
  if (!(d.min_tissue_pct >= 0 && d.min_tissue_pct <= 100)) return "Minimum tissue must be between 0% and 100%.";
  if (!(d.otsu_sensitivity > 0 && d.otsu_sensitivity <= 1)) return "Otsu sensitivity must be above 0 and at most 1.";
  if (!intIn(d.morph_open_px, 0, 99) || !intIn(d.morph_close_px, 0, 99)) return "Morphology sizes must be whole numbers from 0 to 99.";
  if (!intIn(d.min_component_px, 0, 100_000_000)) return "Minimum component size must be a whole number of pixels.";
  if (d.classes.length === 0) return "At least one diagnostic class is required.";
  const names = d.classes.map((k) => k.name.trim().toLowerCase());
  if (names.some((n) => !n)) return "Class names cannot be blank.";
  if (new Set(names).size !== names.length) return "Class names must be unique.";
  const codes = d.classes.map((k) => (k.code ?? "").trim()).filter(Boolean);
  if (codes.some((c) => !/^\d+$/.test(c) || !Number.isSafeInteger(Number(c)))) return "Class IDs must be whole numbers.";
  if (new Set(codes.map(Number)).size !== codes.length) return "Class IDs must be unique.";
  return null;
}

/** Next unused "vN.0" label, e.g. ["v1.0", "v2.0"] -> "v3.0". */

/** "2048 px, stride 1024, 40x, tissue >= 4%" -- the grid a configuration cuts patches with. */
export function gridSummary(c: ConfigVersion): string {
  const size = c.patch_width === c.patch_height ? `${c.patch_width}` : `${c.patch_width}x${c.patch_height}`;
  const stride = c.stride_x === c.stride_y ? `${c.stride_x}` : `${c.stride_x}x${c.stride_y}`;
  const mag = c.target_magnification ? `${c.target_magnification}x` : "default magnification";
  const area = c.min_tissue_fraction <= 0 ? "whole slide" : `tissue >= ${Math.round(c.min_tissue_fraction * 100)}%`;
  return `${size} px, stride ${stride}, ${mag}, ${area}`;
}

