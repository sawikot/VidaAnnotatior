import { describe, expect, it } from "vitest";
import {
  DEFAULT_EXPORT_OPTIONS,
  exportProblem,
  isCombinable,
  normalizeOptions,
  optionsQuery,
  projectDownloadKind,
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
    expect(optionsQuery(opts({ content: "images", imageFormat: "png", masks: true }))).toBe("?content=images&image_format=png&masks=true");
  });

  it("drops image-only settings when no images are exported", () => {
    expect(optionsQuery(opts({ imageFormat: "png", masks: true }))).toBe("");
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
    expect(summaryQuery(opts({ content: "images", imageFormat: "png" }))).toBe("?image_format=png");
    expect(summaryQuery(opts({ imageFormat: "png" }))).toBe("");
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
