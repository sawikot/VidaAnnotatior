import { optionsQuery, summaryQuery, type ExportOptions, type ExportSummary } from "../utils/exportOptions";
import type {
  ConfigUsage,
  ConfigVersion,
  GeometryAnnotation,
  GeometryType,
  OverlappingAnnotation,
  Patch,
  ImageList,
  PatchListResponse,
  PatchStatus,
  Project,
  ProjectDetail,
  Slide,
  SlideBatchImportResult,
  TissueRegion,
  TissueRegions,
  TissueSource,
  WsiFormats,
} from "../types/api";

export const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8088/api";

/** FastAPI returns `detail` as a string for our own errors and as a list of
 * {loc, msg} objects for request-validation errors -- flatten both to text. */
function describeDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => {
        const field = Array.isArray(d.loc) ? d.loc.slice(1).join(".") : "";
        return field ? `${field}: ${d.msg}` : String(d.msg ?? JSON.stringify(d));
      })
      .join("; ");
  }
  return JSON.stringify(detail);
}

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(describeDetail(detail));
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers:
      options.body && !(options.body instanceof FormData)
        ? { "Content-Type": "application/json", ...options.headers }
        : options.headers,
  });
  if (!res.ok) {
    let detail: unknown;
    try {
      detail = await res.json();
    } catch {
      detail = await res.text();
    }
    throw new ApiError(res.status, (detail as { detail?: unknown })?.detail ?? detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
  if (!entries.length) return "";
  return "?" + entries.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`).join("&");
}

// ---- Projects ----
export const listProjects = () => request<Project[]>("/projects");
export const getProject = (id: number) => request<ProjectDetail>(`/projects/${id}`);
export const createProject = (payload: unknown) =>
  request<ProjectDetail>("/projects", { method: "POST", body: JSON.stringify(payload) });
export const updateProject = (id: number, payload: unknown) =>
  request<ProjectDetail>(`/projects/${id}`, { method: "PUT", body: JSON.stringify(payload) });
export const deleteProject = (id: number) => request<void>(`/projects/${id}`, { method: "DELETE" });

// ---- Config versions ----
export const listConfigs = (projectId: number) => request<ConfigVersion[]>(`/projects/${projectId}/configs`);
export const getConfig = (id: number) => request<ConfigVersion>(`/configs/${id}`);
export const createConfig = (projectId: number, payload: unknown) =>
  request<ConfigVersion>(`/projects/${projectId}/configs`, { method: "POST", body: JSON.stringify(payload) });
export const updateConfig = (id: number, payload: unknown) =>
  request<ConfigVersion>(`/configs/${id}`, { method: "PUT", body: JSON.stringify(payload) });
export const lockConfig = (id: number) => request<ConfigVersion>(`/configs/${id}/lock`, { method: "POST" });
export interface ClassSyncItem {
  id?: number;
  name: string;
  color_hex: string;
  hotkey: string | null;
}
export const forkConfig = (
  id: number,
  payload: {
    new_version_label: string;
    overrides?: Record<string, unknown>;
    created_by?: string;
    annotation_classes?: ClassSyncItem[];
  },
) => request<ConfigVersion>(`/configs/${id}/fork`, { method: "POST", body: JSON.stringify(payload) });
export const getConfigUsage = (id: number) => request<ConfigUsage>(`/configs/${id}/usage`);
export const setSlideActiveConfig = (slideId: number, configVersionId: number) =>
  request<Slide>(`/slides/${slideId}/active-config`, {
    method: "PUT",
    body: JSON.stringify({ config_version_id: configVersionId }),
  });

// ---- Slides ----
export const listSlides = (projectId: number) => request<Slide[]>(`/projects/${projectId}/slides`);
export const getSlide = (id: number) => request<Slide>(`/slides/${id}`);
export const deleteSlide = (id: number) => request<void>(`/slides/${id}`, { method: "DELETE" });
export const getWsiFormats = () => request<WsiFormats>("/wsi-formats");

export const importSlideByPath = (projectId: number, path: string, configVersionId?: number) =>
  request<SlideBatchImportResult>(`/projects/${projectId}/slides/import-path`, {
    method: "POST",
    body: JSON.stringify({ path, config_version_id: configVersionId }),
  });

export interface UploadItem {
  file: File;
  /** Path relative to the picked folder (or just the file name). Sent in a
   * manifest because browsers only transmit a bare name per file part -- this
   * is what keeps a folder's structure (e.g. Slide.mrxs next to Slide/). */
  path: string;
}

export class UploadAborted extends Error {
  constructor() {
    super("Upload cancelled");
  }
}

/** Uploads any mix of slide files, folder contents and .zip archives in one
 * request. Uses XMLHttpRequest rather than fetch because fetch can't report
 * upload progress, which matters for multi-gigabyte slides. */
export function uploadSlides(
  projectId: number,
  items: UploadItem[],
  opts: {
    configVersionId?: number;
    onProgress?: (sentBytes: number, totalBytes: number) => void;
    /** Fired when every byte has been sent; the server is then still unpacking/reading. */
    onSent?: () => void;
  } = {},
): { promise: Promise<SlideBatchImportResult>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<SlideBatchImportResult>((resolve, reject) => {
    const form = new FormData();
    for (const item of items) form.append("files", item.file, item.file.name);
    form.append("manifest", JSON.stringify(items.map((i) => i.path)));

    xhr.open("POST", `${API_BASE}/projects/${projectId}/slides/upload${qs({ config_version_id: opts.configVersionId })}`);
    xhr.upload.onprogress = (e) => e.lengthComputable && opts.onProgress?.(e.loaded, e.total);
    xhr.upload.onload = () => opts.onSent?.();
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* non-JSON error body */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as SlideBatchImportResult);
      else reject(new ApiError(xhr.status, (body as { detail?: unknown } | null)?.detail ?? (xhr.responseText || xhr.statusText)));
    };
    xhr.onerror = () => reject(new Error("Network error -- is the backend running?"));
    xhr.onabort = () => reject(new UploadAborted());
    xhr.send(form);
  });
  return { promise, abort: () => xhr.abort() };
}

// A slide's pixels never change, so image URLs carrying its `image_version` are cached by the browser for good
// (the server says so only when the version matches -- an id reused after a delete gets a new one).
const versionParam = (version?: string) => (version ? `&v=${encodeURIComponent(version)}` : "");
export const thumbnailUrl = (slideId: number, maxSize = 512, version?: string) =>
  `${API_BASE}/slides/${slideId}/thumbnail?max_size=${maxSize}${versionParam(version)}`;
export const dynamicPatchUrl = (slideId: number, x: number, y: number, width: number, height: number, level = 0, version?: string) =>
  `${API_BASE}/slides/${slideId}/patch?x=${x}&y=${y}&width=${width}&height=${height}&level=${level}${versionParam(version)}`;
/**
 * A patch preview for cards and thumbnails: the same area, read from the coarsest level of the slide's
 * pyramid that still gives at least `minSide` pixels across -- far less to read and send than full size.
 */
export function patchPreviewUrl(
  slide: Pick<Slide, "id" | "level_downsamples" | "image_version">,
  patch: Pick<Patch, "x" | "y" | "width" | "height" | "width_l0" | "height_l0" | "level">,
  minSide = 320,
): string {
  const downsamples = slide.level_downsamples ?? [];
  let level = patch.level;
  let width = patch.width;
  let height = patch.height;
  downsamples.forEach((ds, i) => {
    const w = Math.round(patch.width_l0 / ds);
    if (w >= minSide && w < width) {
      level = i;
      width = w;
      height = Math.round(patch.height_l0 / ds);
    }
  });
  return dynamicPatchUrl(slide.id, patch.x, patch.y, width, height, level, slide.image_version);
}

/** OpenSeadragon repeats the descriptor's query string on every tile URL, so the version reaches the tiles too. */
export const dziUrl = (slideId: number, version?: string) =>
  `${API_BASE}/slides/${slideId}/dzi.dzi${version ? `?v=${encodeURIComponent(version)}` : ""}`;
export const tissueMaskUrl = (slideId: number) => `${API_BASE}/slides/${slideId}/tissue-mask.png`;
/** The tissue mask's edges as closed rings in Level-0 pixels (outer edges and holes). */
export const getTissueMaskOutline = (slideId: number) => request<{ rings: [number, number][][] }>(`/slides/${slideId}/tissue-mask/outline`);

// ---- Processing ----
export const getTissueRegions = (slideId: number) => request<TissueRegions>(`/slides/${slideId}/tissue-regions`);
export const setTissueRegions = (slideId: number, source: TissueSource, regions: Omit<TissueRegion, "id">[]) =>
  request<TissueRegions>(`/slides/${slideId}/tissue-regions`, {
    method: "PUT",
    body: JSON.stringify({ source, regions: regions.map(({ mode, type, coordinates }) => ({ mode, type, coordinates })) }),
  });
export const detectTissue = (
  slideId: number,
  params: { method?: string; otsu_sensitivity?: number; morph_open_px?: number; morph_close_px?: number; min_component_px?: number },
) =>
  request<{ tissue_area_mm2: number | null; tissue_coverage_pct: number; mask_url: string }>(
    `/slides/${slideId}/detect-tissue`,
    { method: "POST", body: JSON.stringify(params) },
  );
export const generatePatches = (slideId: number, configVersionId: number) =>
  request<{ total_candidates: number; kept: number; excluded: number }>(`/slides/${slideId}/generate-patches`, {
    method: "POST",
    body: JSON.stringify({ config_version_id: configVersionId }),
  });

// ---- Images (image projects) ----
export const listImages = (projectId: number, params: { status?: string; limit?: number; offset?: number } = {}) =>
  request<ImageList>(`/projects/${projectId}/images${qs(params)}`);

// ---- Patches ----
export const listPatches = (
  slideId: number,
  params: { bbox?: string; status?: string; flagged?: boolean; limit?: number; offset?: number } = {},
) => request<PatchListResponse>(`/slides/${slideId}/patches${qs(params)}`);
export const nextPatch = (
  slideId: number,
  currentIndex: number,
  direction: "next" | "prev",
  filter: "any" | "unannotated" | "flagged" | "skipped" = "any",
) =>
  request<Patch | null>(
    `/slides/${slideId}/patches/next${qs({ current_index: currentIndex, direction, filter })}`,
  );
export const getPatch = (id: number) => request<Patch>(`/patches/${id}`);
export const updatePatch = (
  id: number,
  payload: Partial<{
    status: PatchStatus;
    patch_label: string | null;
    unsure: boolean;
    flagged: boolean;
    excluded: boolean;
    notes: string | null;
    reviewed_by: string | null;
  }>,
) => request<Patch>(`/patches/${id}`, { method: "PUT", body: JSON.stringify(payload) });

// ---- Annotations ----
export const listPatchAnnotations = (patchId: number) =>
  request<GeometryAnnotation[]>(`/patches/${patchId}/annotations`);
/** Annotations drawn in other patches that reach into this one (patches overlap when stride < size). */
export const listOverlappingAnnotations = (patchId: number) =>
  request<OverlappingAnnotation[]>(`/patches/${patchId}/overlapping-annotations`);
/** "patch": drawn in a patch; "slide": drawn on the whole slide (WSI mode); "all" (default): both. */
export const listSlideAnnotations = (slideId: number, scope: "all" | "patch" | "slide" = "all") =>
  request<GeometryAnnotation[]>(`/slides/${slideId}/annotations${scope === "all" ? "" : `?scope=${scope}`}`);
/** An annotation drawn directly on the whole slide: Level-0 pixel coordinates, belonging to no patch. */
export const createSlideAnnotation = (
  slideId: number,
  payload: {
    type: GeometryType;
    class_id: number | null;
    coordinates_level0: [number, number][];
    created_by?: string;
    notes?: string;
    unsure?: boolean;
    flagged?: boolean;
  },
) => request<GeometryAnnotation>(`/slides/${slideId}/annotations`, { method: "POST", body: JSON.stringify(payload) });
export const createAnnotation = (
  patchId: number,
  payload: {
    type: GeometryType;
    class_id: number | null;
    coordinates_patch_local: [number, number][];
    created_by?: string;
    notes?: string;
    unsure?: boolean;
    flagged?: boolean;
    excluded?: boolean;
  },
) => request<GeometryAnnotation>(`/patches/${patchId}/annotations`, { method: "POST", body: JSON.stringify(payload) });
export const updateAnnotation = (
  id: number,
  payload: Partial<{
    class_id: number | null;
    coordinates_patch_local: [number, number][];
    /** For a slide-level annotation (which has no patch-local coordinates). */
    coordinates_level0: [number, number][];
    notes: string | null;
    unsure: boolean;
    flagged: boolean;
    excluded: boolean;
  }>,
) => request<GeometryAnnotation>(`/annotations/${id}`, { method: "PUT", body: JSON.stringify(payload) });
export const deleteAnnotation = (id: number) => request<void>(`/annotations/${id}`, { method: "DELETE" });

export interface ImportAnnotationsResult {
  total: number;
  imported: number;
  skipped_no_matching_patch: number;
  skipped_unknown_class: number;
  skipped_duplicate: number;
  /** Entries with a malformed or unknown shape (older servers don't send this). */
  skipped_invalid_shape?: number;
}
export const importAnnotations = (
  slideId: number,
  payload: { annotations: unknown[]; config_version_id?: number; created_by?: string },
) =>
  request<ImportAnnotationsResult>(`/slides/${slideId}/import-annotations`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

// ---- Export ----
export const exportSlideUrl = (slideId: number, formatId: string, options?: ExportOptions) =>
  `${API_BASE}/slides/${slideId}/export/${formatId}${options ? optionsQuery(options) : ""}`;
/** One file per processed slide of the project (zipped), or one combined file; see `projectDownloadKind`. */
export const exportProjectUrl = (projectId: number, formatId: string, options?: ExportOptions) =>
  `${API_BASE}/projects/${projectId}/export/${formatId}${options ? optionsQuery(options, { project: true }) : ""}`;

/** What an export with these options would cover (counts only; nothing is rendered). */
export const getExportSummary = (scope: "slide" | "project", id: number, options: ExportOptions) =>
  request<ExportSummary>(`/${scope === "slide" ? "slides" : "projects"}/${id}/export-summary${summaryQuery(options)}`);

/**
 * Downloads an export through fetch and resolves with its file name. Going through fetch rather than
 * navigating means a refusal (nothing processed yet, too many images ...) surfaces as a thrown message
 * instead of replacing the page with a JSON error. Server-side detail is passed through. The file is
 * held in memory, so use `navigateDownload` for large ones.
 */
export async function downloadExport(url: string, fallbackName: string): Promise<string> {
  let res: Response;
  try {
    res = await fetch(url);
  } catch {
    throw new Error("Export failed: could not reach the server");
  }
  if (!res.ok) {
    const detail = await res.json().then((b) => b.detail).catch(() => null);
    throw new Error(typeof detail === "string" ? detail : "Export failed");
  }
  const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? fallbackName;
  const objectUrl = URL.createObjectURL(await res.blob());
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = name;
  link.click();
  URL.revokeObjectURL(objectUrl);
  return name;
}

export const downloadProjectExport = (projectId: number, formatId: string, options?: ExportOptions) =>
  downloadExport(exportProjectUrl(projectId, formatId, options), `project_${formatId}_all_slides.zip`);

/** Hands the URL to the browser, which streams the response straight to disk (no copy in memory). */
export function navigateDownload(url: string): void {
  window.location.assign(url);
}
