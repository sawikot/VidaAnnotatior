import { describe, expect, it } from "vitest";
import type { ConfigVersion } from "../../types/api";
import { diffDraft, draftFromConfig, validateDraft } from "./configDraft";

const config = {
  id: 1,
  project_id: 1,
  parent_version_id: null,
  version_label: "v1.0",
  status: "draft",
  title: null,
  created_by: null,
  config_hash: "x",
  coordinate_system: "level0",
  target_magnification: 20,
  target_level: null,
  mpp_handling: "auto",
  patch_width: 512,
  patch_height: 512,
  stride_x: 512,
  stride_y: 512,
  min_tissue_fraction: 0.6,
  allow_partial_patches: false,
  include_edge_patches: true,
  tissue_method: "hsv_otsu",
  tissue_params: { otsu_sensitivity: 0.65, morph_open_px: 3, morph_close_px: 5, min_component_px: 400 },
  enabled_tools: ["polygon", "rectangle"],
  allow_skip: true,
  allow_unsure: true,
  require_annotation: false,
  reviewer_mode: false,
  created_at: "",
  updated_at: "",
  annotation_classes: [
    { id: 10, name: "Tumor", color_hex: "#dc2626", hotkey: "1", code: null, order_index: 0 },
    { id: 11, name: "Stroma", color_hex: "#16a34a", hotkey: null, code: null, order_index: 1 },
  ],
} as ConfigVersion;

describe("diffDraft", () => {
  it("reports no change for an untouched draft", () => {
    const d = diffDraft(draftFromConfig(config), config);
    expect(d.changed).toBe(false);
    expect(d.fields).toEqual({});
    expect(d.classes).toBeNull();
  });

  it("flags geometry edits as critical and sends only changed fields", () => {
    const draft = { ...draftFromConfig(config), stride_x: 256, min_tissue_pct: 70 };
    const d = diffDraft(draft, config);
    expect(d.fields).toEqual({ stride_x: 256, min_tissue_fraction: 0.7 });
    expect(d.touchedCritical).toEqual(["Stride X", "Minimum tissue fraction"]);
  });

  it("treats QC, tool, and tissue-parameter edits as non-critical", () => {
    const draft = { ...draftFromConfig(config), allow_skip: false, enabled_tools: ["polygon"], otsu_sensitivity: 0.5 };
    const d = diffDraft(draft, config);
    expect(d.touchedCritical).toEqual([]);
    expect(d.fields.allow_skip).toBe(false);
    expect(d.fields.enabled_tools).toEqual(["polygon"]);
    expect(d.fields.tissue_params).toMatchObject({ otsu_sensitivity: 0.5, morph_open_px: 3 });
  });

  it("sends the full class list, keeping ids, only when classes changed", () => {
    const draft = draftFromConfig(config);
    draft.classes[0].name = "Invasive Tumor";
    draft.classes.push({ name: "Normal", color_hex: "#2563eb" });
    const d = diffDraft(draft, config);
    expect(d.changed).toBe(true);
    expect(d.touchedCritical).toEqual([]);
    expect(d.classes).toEqual([
      { id: 10, name: "Invasive Tumor", color_hex: "#dc2626", code: null },
      { id: 11, name: "Stroma", color_hex: "#16a34a", code: null },
      { id: undefined, name: "Normal", color_hex: "#2563eb", code: null },
    ]);
  });

  it("sends Class IDs of any size as numbers and rejects duplicates", () => {
    const draft = draftFromConfig(config);
    draft.classes[0].code = "904558";
    expect(diffDraft(draft, config).classes?.[0].code).toBe(904558);
    expect(validateDraft(draft)).toBeNull();
    draft.classes[1].code = "904558";
    expect(validateDraft(draft)).toBe("Class IDs must be unique.");
    draft.classes[1].code = "12a";
    expect(validateDraft(draft)).toBe("Class IDs must be whole numbers.");
  });

  it("does not report a change when a blank Class ID stays blank (it is optional)", () => {
    const draft = draftFromConfig(config);
    draft.classes[1].code = "  ";
    expect(validateDraft(draft)).toBeNull();
    expect(diffDraft(draft, config).classes).toBeNull();
  });
});

describe("validateDraft", () => {
  it("accepts the original config", () => {
    expect(validateDraft(draftFromConfig(config))).toBeNull();
  });

  it.each([
    [{ patch_width: 8 }, /Patch width/],
    [{ stride_x: 0 }, /Stride/],
    [{ patch_height: 512.5 }, /Patch width\/height/],
    [{ min_tissue_pct: 120 }, /Minimum tissue/],
    [{ target_magnification: 0 }, /magnification/],
    [{ otsu_sensitivity: NaN }, /Otsu/],
    [{ morph_open_px: -1 }, /Morphology/],
  ])("rejects %j", (patch, message) => {
    expect(validateDraft({ ...draftFromConfig(config), ...patch })).toMatch(message);
  });

  it("rejects empty, duplicate, and blank classes", () => {
    const base = draftFromConfig(config);
    expect(validateDraft({ ...base, classes: [] })).toMatch(/At least one/);
    expect(validateDraft({ ...base, classes: [{ name: "A", color_hex: "#000000" }, { name: " a ", color_hex: "#000000" }] })).toMatch(/unique/);
    expect(validateDraft({ ...base, classes: [{ name: " ", color_hex: "#000000" }] })).toMatch(/blank/);
  });
});
