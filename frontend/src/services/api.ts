import { optionsQuery, selectionQuery, summaryQuery, type ExportOptions, type ExportSummary } from "../utils/exportOptions";
import type {
  ConfigUsage,
  ConfigVersion,
  GeometryAnnotation,
  GeometryType,
  GridRemoval,
  GridSpec,
  OverlappingAnnotation,
  PatchGrid,
  ProjectGrid,
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

// Same address as the page (the dev server proxies /api to the backend; in production the backend
// serves the page), so the sign-in cookie reaches every request -- images and tiles included.
export const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

let onUnauthorized: () => void = () => undefined;
/** Called when the server answers 401 (not signed in, or the session ended). */
export function setUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler;
}

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
    if (res.status === 401 && !path.startsWith("/auth/")) onUnauthorized();
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
export const getConfig = (id: number) => request<ConfigVersion>(`/configs/${id}`);
export const updateConfig = (id: number, payload: unknown) =>
  request<ConfigVersion>(`/configs/${id}`, { method: "PUT", body: JSON.stringify(payload) });
export interface ClassSyncItem {
  id?: number;
  name: string;
  color_hex: string;
  code: number | null;
}
export const getConfigUsage = (id: number) => request<ConfigUsage>(`/configs/${id}/usage`);

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
export const listGrids = (slideId: number) => request<PatchGrid[]>(`/slides/${slideId}/grids`);
export const listProjectGrids = (projectId: number) => request<ProjectGrid[]>(`/projects/${projectId}/grids`);
/** Remove a patch size (from every slide, or one); its annotations are kept as whole-slide annotations. */
/** Cut every slide with tissue into another patch size (optionally making it the project's grid). */
export const addProjectGrid = (projectId: number, grid: GridSpec, makeDefault: boolean) =>
  request<{ grid_key: string; grid_label: string; slides: number; patches: number; skipped: { slide_id: number; slide: string; reason: string }[] }>(
    `/projects/${projectId}/grids`,
    { method: "POST", body: JSON.stringify({ grid, make_default: makeDefault }) },
  );
export const removeProjectGrid = (projectId: number, gridKey: string) =>
  request<GridRemoval>(`/projects/${projectId}/grids/${encodeURIComponent(gridKey)}`, { method: "DELETE" });
export const removeSlideGrid = (slideId: number, gridKey: string) =>
  request<GridRemoval>(`/slides/${slideId}/grids/${encodeURIComponent(gridKey)}`, { method: "DELETE" });
export const setActiveGrid = (slideId: number, gridKey: string) =>
  request<Slide>(`/slides/${slideId}/active-grid`, { method: "PUT", body: JSON.stringify({ grid_key: gridKey }) });
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
/** Cut the slide's patches -- with the configuration's grid, or with `grid` (another patch size ...). */
/** Without `grid` the slide is re-cut at the patch size it is on (the project's for a slide with none).
 * `wholeSlide`: true -- the entire slide up to its edges, whatever the tissue (no tissue detection needed);
 * false -- only the tissue; left out -- as the grid is. */
export const generatePatches = (slideId: number, configVersionId: number, grid?: GridSpec, wholeSlide?: boolean) =>
  request<{
    total_candidates: number;
    kept: number;
    excluded: number;
    grid_key: string;
    grid_label: string;
    preserved: number;
    /** The slide's earlier grids this one replaced, and the annotations moved from their patches to the whole slide. */
    replaced_grids: number;
    replaced_patches: number;
    annotations_moved_to_slide: number;
  }>(`/slides/${slideId}/generate-patches`, {
    method: "POST",
    body: JSON.stringify({ config_version_id: configVersionId, ...(grid ? { grid } : {}), ...(wholeSlide !== undefined ? { whole_slide: wholeSlide } : {}) }),
  });

// ---- Images (image projects) ----
export const listImages = (projectId: number, params: { status?: string; limit?: number; offset?: number } = {}) =>
  request<ImageList>(`/projects/${projectId}/images${qs(params)}`);

// ---- Patches ----
export const listPatches = (
  slideId: number,
  params: {
    bbox?: string;
    status?: string;
    flagged?: boolean;
    /** A patch label (any case); "-" for patches with none. */
    label?: string;
    sort?: PatchSort;
    limit?: number;
    offset?: number;
  } = {},
) => request<PatchListResponse>(`/slides/${slideId}/patches${qs(params)}`);

export type PatchSort = "index" | "tissue_desc" | "tissue_asc";
/** The label filter's value for patches that have no label. */
export const NO_LABEL = "-";

/** How many patches carry each label (null: no label), in the slide's current patch size. */
export const getPatchLabelCounts = (slideId: number) =>
  request<{ label: string | null; count: number }[]>(`/slides/${slideId}/patches/label-counts`);

/** The same label for many patches of one slide at once (null removes it); as setting it on each one. */
export const labelPatches = (slideId: number, patchIds: number[], label: string | null) =>
  request<{ updated: number }>(`/slides/${slideId}/patches/label`, { method: "POST", body: JSON.stringify({ patch_ids: patchIds, label }) });
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
  imported_to_patches: number;
  imported_on_slide: number;
  skipped_no_matching_patch: number;
  skipped_unknown_class: number;
  skipped_duplicate: number;
  skipped_invalid_shape: number;
  skipped_outside_slide: number;
  /** Their label was mapped to "skip". */
  skipped_by_choice: number;
}
/** What to do with the shapes carrying one label of the file: a class id, no class, or leave them out. */
export type ImportLabelTarget = number | "skip" | "unlabeled";
export interface ImportEntry {
  type: GeometryType;
  label: string | null;
  coordinates: number[][];
  source_patch: { x: number; y: number } | null;
  unsure: boolean;
  flagged: boolean;
}
/** An annotation file read and converted by the server, before anything is saved. */
export interface ParsedAnnotations {
  format: string;
  format_name: string;
  annotations: ImportEntry[];
  /** `label` is "" for shapes without one; `class_id` is the class of that name, if any. */
  labels: { label: string; count: number; class_id: number | null }[];
  classes: { id: number; name: string; color_hex: string; code: number | null }[];
  shape_counts: Record<string, number>;
  linked_to_patches: number;
  outside_slide: number;
  bounds: [number, number, number, number] | null;
  slide_size: [number | null, number | null];
  image_project: boolean;
  /** Every coordinate was multiplied by this; `scale_note` says why when it was worked out from the file. */
  scale: number;
  scale_auto: boolean;
  scale_note: string | null;
  unreadable: Record<string, number>;
  warnings: string[];
}
/** `scale` left out: the server works it out from the file (1 unless the file shows otherwise). */
export const parseAnnotationFile = (slideId: number, file: File, scale?: number) => {
  const form = new FormData();
  form.append("file", file);
  if (scale !== undefined) form.append("scale", String(scale));
  return request<ParsedAnnotations>(`/slides/${slideId}/import-annotations/parse`, { method: "POST", body: form });
};
export const importAnnotations = (
  slideId: number,
  payload: {
    annotations: unknown[];
    config_version_id?: number;
    created_by?: string;
    label_map?: Record<string, ImportLabelTarget>;
    assign_to_patches?: boolean;
  },
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
export const getExportSummary = (scope: "slide" | "project", id: number, options: ExportOptions, formats?: string | string[], slides?: number[]) =>
  request<ExportSummary>(`/${scope === "slide" ? "slides" : "projects"}/${id}/export-summary${summaryQuery(options, formats, slides)}`);

/** The export screen's download: the chosen slides, in one or several formats (see `selectionDownloadKind`). */
export const exportSelectionUrl = (projectId: number, options: ExportOptions, formats: string[], slides: number[], allSlides: number) =>
  `${API_BASE}/projects/${projectId}/export${selectionQuery(options, formats, slides, allSlides)}`;

/**
 * Starts a download the browser itself carries out: the server's file streams straight to disk, under
 * the server's file name, with no copy in memory. Call it directly from the click handler -- Chrome only
 * lets a page start a download while it is still handling the user's click, so fetching the file first
 * and saving it afterwards is silently blocked there for anything that takes a few seconds to build.
 */
export function startDownload(url: string): void {
  const link = document.createElement("a");
  link.href = url;
  link.rel = "noopener";
  link.style.display = "none";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

// ---- Signing in, users, project members ----
export type UserRole = "admin" | "manager" | "annotator";

export interface AuthUser {
  id: number;
  email: string;
  name: string;
  role: UserRole;
  is_active: boolean;
  has_password: boolean;
  project_count: number;
}

export interface ProjectMemberInfo {
  id: number;
  name: string;
  email: string;
  role: UserRole;
  is_active: boolean;
}

export interface UserCreated {
  user: AuthUser;
  /** Present when no password was given: the person sets one from /set-password?token=... */
  password_link_token: string | null;
}

const post = <T,>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) });

export const getAuthStatus = () => request<{ needs_setup: boolean; user: AuthUser | null }>("/auth/status");
export const setupAdmin = (body: { name: string; email: string; password: string }) => post<AuthUser>("/auth/setup", body);
export const login = (email: string, password: string) => post<AuthUser>("/auth/login", { email, password });
export const logout = () => request<void>("/auth/logout", { method: "POST" });
export const changePassword = (current_password: string, new_password: string) =>
  post<void>("/auth/password", { current_password, new_password });
export const getPasswordLink = (token: string) => request<AuthUser>(`/auth/password-link/${encodeURIComponent(token)}`);
export const submitPasswordLink = (token: string, password: string) => post<AuthUser>("/auth/password-link", { token, password });

export const listUsers = () => request<AuthUser[]>("/users");
export const createUser = (body: { name: string; email: string; role: UserRole; password?: string }) => post<UserCreated>("/users", body);
export const updateUser = (id: number, body: { name?: string; role?: UserRole; is_active?: boolean }) =>
  request<AuthUser>(`/users/${id}`, { method: "PUT", body: JSON.stringify(body) });
export const newPasswordLink = (id: number) => post<UserCreated>(`/users/${id}/password-link`, {});

export const listMembers = (projectId: number) => request<ProjectMemberInfo[]>(`/projects/${projectId}/members`);
export const addMember = (projectId: number, userId: number) => post<ProjectMemberInfo[]>(`/projects/${projectId}/members`, { user_id: userId });
export const removeMember = (projectId: number, userId: number) =>
  request<ProjectMemberInfo[]>(`/projects/${projectId}/members/${userId}`, { method: "DELETE" });

/** The address a password link opens (shown to the administrator to pass on). */
export const passwordLinkUrl = (token: string) => `${window.location.origin}/set-password?token=${encodeURIComponent(token)}`;
