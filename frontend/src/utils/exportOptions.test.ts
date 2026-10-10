import { describe, expect, it } from "vitest";
import {
  DEFAULT_EXPORT_OPTIONS,
  exportProblem,
  isCombinable,
  normalizeOptions,
  optionsQuery,
  projectDownloadKind,
  selectionDownloadKind,
  selectionProblem,
  selectionQuery,
  summaryQuery,
  type ExportOptions,
  type ExportSummary,
} from "./exportOptions";

const opts = (over: Partial<ExportOptions> = {}): ExportOptions => ({ ...DEFAULT_EXPORT_OPTIONS, ...over });
const summary = (over: Partial<ExportSummary> = {}): ExportSummary => ({
  slides: 1,
  patches: 10,
  annotations: 4,
  images: 10,
  approx_image_bytes: 1000,
  max_images: 50_000,
  ...over,
});

describe("optionsQuery", () => {
  it("is empty for the defaults, so plain downloads keep their plain URLs", () => {
    expect(optionsQuery(opts())).toBe("");
  });

  it("carries only what differs from the defaults", () => {
    expect(optionsQuery(opts({ patches: "all" }))).toBe("?patches=all");
    expect(optionsQuery(opts({ patches: "empty", content: "images" }))).toBe("?patches=empty&content=images");
    expect(optionsQuery(opts({ content: "images", imageFormat: "jpg", masks: true }))).toBe("?content=images&image_format=jpg&masks=true");
  });

  it("drops image-only settings when no images are exported", () => {
    expect(optionsQuery(opts({ imageFormat: "jpg", masks: true }))).toBe("");
  });

  it("sends combine only for whole-project downloads and only when chosen", () => {
    expect(optionsQuery(opts({ combine: false }))).toBe("");
    expect(optionsQuery(opts({ combine: false }), { project: true })).toBe("?combine=false");
    expect(optionsQuery(opts({ combine: true }), { project: true })).toBe("?combine=true");
    expect(optionsQuery(opts(), { project: true })).toBe("");
  });
});

describe("summaryQuery", () => {
  it("passes the scope, and the image format only when images are exported", () => {
    expect(summaryQuery(opts())).toBe("");
    expect(summaryQuery(opts({ patches: "reviewed" }))).toBe("?patches=reviewed");
    expect(summaryQuery(opts({ content: "images", imageFormat: "jpg" }))).toBe("?image_format=jpg");
    expect(summaryQuery(opts({ imageFormat: "jpg" }))).toBe("");
  });
});

describe("normalizeOptions", () => {
  it("switches masks off when there are no images to hold them", () => {
    expect(normalizeOptions(opts({ masks: true })).masks).toBe(false);
    expect(normalizeOptions(opts({ masks: true, content: "images" })).masks).toBe(true);
  });
});

describe("projectDownloadKind (mirrors the server)", () => {
  it("is a zip for per-slide formats and for anything with images", () => {
    expect(projectDownloadKind("wsi", "wsi_json", opts())).toBe("zip");
    expect(projectDownloadKind("image", "geojson", opts())).toBe("zip");
    expect(projectDownloadKind("wsi", "coco", opts({ content: "images" }))).toBe("zip");
  });

  it("is one file for dataset formats in an image project, or when combining is asked for", () => {
    expect(projectDownloadKind("image", "coco", opts())).toBe("file");
    expect(projectDownloadKind("image", "patch_csv", opts())).toBe("file");
    expect(projectDownloadKind("wsi", "coco", opts())).toBe("zip");
    expect(projectDownloadKind("wsi", "coco", opts({ combine: true }))).toBe("file");
    expect(projectDownloadKind("image", "coco", opts({ combine: false }))).toBe("zip");
    expect(projectDownloadKind("wsi", "geojson", opts({ combine: true }))).toBe("zip"); // not combinable
  });

  it("knows which formats can be combined", () => {
    expect(["coco", "patch_csv", "stats_csv"].every(isCombinable)).toBe(true);
    expect(isCombinable("geojson") || isCombinable("wsi_json")).toBe(false);
  });
});

describe("exportProblem", () => {
  it("has nothing to say before the counts arrive, or when all is well", () => {
    expect(exportProblem(null, opts({ content: "images" }))).toBeNull();
    expect(exportProblem(summary(), opts({ content: "images" }))).toBeNull();
  });

  it("refuses an empty selection", () => {
    expect(exportProblem(summary({ slides: 0 }), opts())).toMatch(/no slide has a patch grid/i);
    expect(exportProblem(summary({ images: 0 }), opts({ content: "images" }))).toMatch(/no images to write/i);
    expect(exportProblem(summary({ images: 0 }), opts())).toBeNull(); // an annotation file can still be empty-but-valid
  });

  it("explains a selection that is too big for one download", () => {
    const message = exportProblem(summary({ images: 60_000 }), opts({ content: "images" }));
    expect(message).toContain("60,000");
    expect(message).toContain("50,000");
    expect(exportProblem(summary({ images: 60_000 }), opts())).toBeNull(); // only images are capped
  });
});

describe("custom export grid", () => {
  const grid = { patch_width: 512, patch_height: 512, stride_x: 256, stride_y: 256, target_magnification: 20, min_tissue_fraction: 0.5, include_edge_patches: false, allow_partial_patches: false };

  it("is sent as the server's grid key, in downloads and in the counts", () => {
    const o = { ...DEFAULT_EXPORT_OPTIONS, grid };
    expect(optionsQuery(o)).toBe("?grid=512x512_s256x256_m20_t0.5");
    expect(summaryQuery(o)).toBe("?grid=512x512_s256x256_m20_t0.5");
    expect(optionsQuery(DEFAULT_EXPORT_OPTIONS)).toBe("");
  });

  it("an invalid grid is reported and not sent", () => {
    const o = { ...DEFAULT_EXPORT_OPTIONS, grid: { ...grid, patch_width: 4 } };
    expect(optionsQuery(o)).toBe("");
    expect(exportProblem(null, o)).toMatch(/Patch size/);
  });
});

describe("the export screen's download", () => {
  it("names the formats, and the slides only when not all are chosen", () => {
    expect(selectionQuery(opts(), ["coco", "patch_csv"], [1, 2, 3], 3)).toBe("?formats=coco%2Cpatch_csv");
    expect(selectionQuery(opts({ content: "images" }), ["coco"], [2], 3)).toBe("?content=images&formats=coco&slides=2");
  });

  it("is one file only for one format without images, for one slide or combined", () => {
    expect(selectionDownloadKind("wsi", ["wsi_json"], opts(), 1)).toBe("file");
    expect(selectionDownloadKind("wsi", ["wsi_json"], opts(), 2)).toBe("zip");
    expect(selectionDownloadKind("wsi", ["coco"], opts({ combine: true }), 2)).toBe("file");
    expect(selectionDownloadKind("wsi", ["coco", "patch_csv"], opts(), 1)).toBe("zip");
    expect(selectionDownloadKind("wsi", ["coco"], opts({ content: "images" }), 1)).toBe("zip");
    expect(selectionDownloadKind("wsi", ["wsi_json"], opts({ split: true }), 1)).toBe("zip");
    expect(selectionQuery(opts({ split: true }), ["coco"], [1], 1)).toBe("?formats=coco&split=true");
  });

  it("needs a slide and a file", () => {
    expect(selectionProblem(summary(), opts(), ["coco"], 0)).toMatch(/at least one slide/);
    expect(selectionProblem(summary(), opts(), [], 2)).toMatch(/at least one file/);
    expect(selectionProblem(summary(), opts(), ["coco"], 2)).toBeNull();
  });

  it("counts only the chosen slides", () => {
    expect(summaryQuery(opts(), ["coco"], [4, 5])).toBe("?slides=4%2C5");
    expect(summaryQuery(opts(), ["patch_classification", "coco"])).toBe("?format=patch_classification%2Ccoco");
  });
});
