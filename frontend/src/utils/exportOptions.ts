import type { GridSpec } from "../types/api";
import { gridKey, gridProblem } from "./gridKey";

/** What is chosen at export time besides the format. Mirrors backend/app/services/exporter/options.py. */
export type PatchScope = "annotated" | "all" | "empty" | "reviewed";
export type ExportContent = "annotations" | "images";
export type ImageFormat = "jpg" | "png";
/** Patch classification: what happens to patches with no clear class. */
export type UnlabeledMode = "skip" | "folder";

export interface ExportOptions {
  patches: PatchScope;
  content: ExportContent;
  imageFormat: ImageFormat;
  masks: boolean;
  /** One file for the whole project instead of one per slide; null lets the server decide. */
  combine: boolean | null;
  /** Export in this patch grid instead of the one each slide was annotated in; null: as annotated. */
  grid: GridSpec | null;
  /** Patch classification: share of a patch a drawn class must cover to name it (0.5-1). */
  minCoverage: number;
  unlabeled: UnlabeledMode;
  /** Patch classification: folders for labels that are not a class (Mixed, Artifact / Background). */
  otherLabels: boolean;
  /** Export screen: sort the download into train / val / test folders, by the project's split. */
  split: boolean;
}

export const DEFAULT_EXPORT_OPTIONS: ExportOptions = {
  patches: "annotated",
  content: "annotations",
  imageFormat: "jpg",
  masks: false,
  combine: null,
  grid: null,
  minCoverage: 0.9,
  unlabeled: "skip",
  otherLabels: true,
  split: false,
};

export const CLASSIFICATION_FORMAT = "patch_classification";

/** The patch-classification choices that differ from the defaults (other formats ignore them). */
function addClassification(o: ExportOptions, params: URLSearchParams) {
  if (o.minCoverage !== DEFAULT_EXPORT_OPTIONS.minCoverage) params.set("min_coverage", String(o.minCoverage));
  if (o.unlabeled !== DEFAULT_EXPORT_OPTIONS.unlabeled) params.set("unlabeled", o.unlabeled);
  if (o.otherLabels !== DEFAULT_EXPORT_OPTIONS.otherLabels) params.set("other_labels", String(o.otherLabels));
}

export interface ExportSummary {
  slides: number;
  patches: number;
  annotations: number;
  images: number;
  approx_image_bytes: number;
  max_images: number;
}

/** Formats that are datasets rather than per-slide coordinate files, so they can be combined. */
const COMBINABLE_FORMATS = new Set(["coco", "patch_csv", "stats_csv", CLASSIFICATION_FORMAT]);

/** Masks only exist alongside images; keep the options consistent whatever was clicked last. */
export function normalizeOptions(o: ExportOptions): ExportOptions {
  return o.content === "images" ? o : { ...o, masks: false };
}

/** Query string (with leading "?", or "") carrying only what differs from the defaults. */
export function optionsQuery(o: ExportOptions, opts: { project?: boolean } = {}): string {
  const n = normalizeOptions(o);
  const params = new URLSearchParams();
  if (n.patches !== DEFAULT_EXPORT_OPTIONS.patches) params.set("patches", n.patches);
  if (n.content !== DEFAULT_EXPORT_OPTIONS.content) params.set("content", n.content);
  if (n.content === "images") {
    if (n.imageFormat !== DEFAULT_EXPORT_OPTIONS.imageFormat) params.set("image_format", n.imageFormat);
    if (n.masks) params.set("masks", "true");
  }
  if (opts.project && n.combine !== null) params.set("combine", String(n.combine));
  if (n.grid && !gridProblem(n.grid)) params.set("grid", gridKey(n.grid));
  addClassification(n, params);
  const text = params.toString();
  return text ? `?${text}` : "";
}

/**
 * Query for the counts endpoint (it only needs the scope and the image format for the size estimate --
 * and, for patch classification, which patches get a class and so an image). `formats` may be one
 * format or several; `slides` limits the counts to those slides (the whole project when omitted).
 */
export function summaryQuery(o: ExportOptions, formats?: string | string[], slides?: number[]): string {
  const params = new URLSearchParams();
  if (o.patches !== DEFAULT_EXPORT_OPTIONS.patches) params.set("patches", o.patches);
  if (o.content === "images" && o.imageFormat !== DEFAULT_EXPORT_OPTIONS.imageFormat) params.set("image_format", o.imageFormat);
  if (o.grid && !gridProblem(o.grid)) params.set("grid", gridKey(o.grid));
  const list = typeof formats === "string" ? [formats] : (formats ?? []);
  if (list.includes(CLASSIFICATION_FORMAT)) {
    params.set("format", list.join(","));
    addClassification(o, params);
  }
  if (slides) params.set("slides", slides.join(","));
  const text = params.toString();
  return text ? `?${text}` : "";
}

/** What a whole-project download arrives as -- the same rule the server applies. */
export function projectDownloadKind(projectType: string | undefined, format: string, o: ExportOptions): "zip" | "file" {
  if (o.content === "images") return "zip";
  const combine = o.combine ?? projectType === "image";
  return combine && COMBINABLE_FORMATS.has(format) ? "file" : "zip";
}

export function isCombinable(format: string): boolean {
  return COMBINABLE_FORMATS.has(format);
}

/** Why the chosen options can't be downloaded, or null. Checked before the user waits for a download. */
export function exportProblem(summary: ExportSummary | null, o: ExportOptions): string | null {
  if (o.grid && gridProblem(o.grid)) return gridProblem(o.grid);
  if (!summary) return null;
  if (summary.slides === 0) return "Nothing to export yet: no slide has a patch grid.";
  if (o.content === "images") {
    if (summary.images === 0) return "No patches match this selection, so there are no images to write.";
    if (summary.images > summary.max_images) {
      return `${summary.images.toLocaleString()} images is more than one download holds (${summary.max_images.toLocaleString()}). Choose annotated or reviewed patches only, or export one slide at a time.`;
    }
  }
  return null;
}

/**
 * Query for the export screen's download (GET /projects/{id}/export): the chosen formats and slides plus
 * the options. `slides` is left out when every slide of the project is chosen, which keeps the URL short.
 */
export function selectionQuery(o: ExportOptions, formats: string[], slides: number[], allSlides: number): string {
  const params = new URLSearchParams(optionsQuery(o, { project: true }).replace(/^\?/, ""));
  params.set("formats", formats.join(","));
  if (slides.length !== allSlides) params.set("slides", slides.join(","));
  if (o.split) params.set("split", "true");
  return `?${params.toString()}`;
}

/** What the export screen's download arrives as -- the same rule the server applies. */
export function selectionDownloadKind(projectType: string | undefined, formats: string[], o: ExportOptions, slideCount: number): "zip" | "file" {
  if (o.content === "images" || formats.length !== 1 || o.split) return "zip"; // a split is one folder per set
  if (slideCount === 1) return "file";
  const combine = o.combine ?? projectType === "image";
  return combine && COMBINABLE_FORMATS.has(formats[0]) ? "file" : "zip";
}

/** Why the export screen can't download yet, or null. */
export function selectionProblem(summary: ExportSummary | null, o: ExportOptions, formats: string[], slideCount: number, noun = "slide"): string | null {
  if (slideCount === 0) return `Choose at least one ${noun} to export.`;
  if (formats.length === 0) return "Choose at least one file to download (JSON, CSV).";
  return exportProblem(summary, o);
}
