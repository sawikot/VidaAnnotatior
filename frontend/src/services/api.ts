import type {
  ConfigVersion,
  GeometryAnnotation,
  GeometryType,
  Patch,
  PatchListResponse,
  PatchStatus,
  Project,
  ProjectDetail,
  Slide,
} from "../types/api";

export const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8088/api";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
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
export const forkConfig = (
  id: number,
  payload: { new_version_label: string; overrides?: Record<string, unknown>; created_by?: string },
) => request<ConfigVersion>(`/configs/${id}/fork`, { method: "POST", body: JSON.stringify(payload) });

// ---- Slides ----
export const listSlides = (projectId: number) => request<Slide[]>(`/projects/${projectId}/slides`);
export const getSlide = (id: number) => request<Slide>(`/slides/${id}`);
export const deleteSlide = (id: number) => request<void>(`/slides/${id}`, { method: "DELETE" });
export const uploadSlide = (projectId: number, file: File, configVersionId?: number) => {
  const form = new FormData();
  form.append("file", file);
  const q = qs({ config_version_id: configVersionId });
  return request<Slide>(`/projects/${projectId}/slides/upload${q}`, { method: "POST", body: form });
};
export const importSlideByPath = (projectId: number, path: string, configVersionId?: number) =>
  request<Slide>(`/projects/${projectId}/slides/import-path`, {
    method: "POST",
    body: JSON.stringify({ path, config_version_id: configVersionId }),
  });
export const createDemoSlide = (projectId: number, filename?: string, configVersionId?: number) =>
  request<Slide>(`/projects/${projectId}/slides/demo`, {
    method: "POST",
    body: JSON.stringify({ filename: filename ?? "demo_slide.svs", config_version_id: configVersionId }),
  });

export const thumbnailUrl = (slideId: number, maxSize = 512) => `${API_BASE}/slides/${slideId}/thumbnail?max_size=${maxSize}`;
export const dynamicPatchUrl = (slideId: number, x: number, y: number, width: number, height: number, level = 0) =>
  `${API_BASE}/slides/${slideId}/patch?x=${x}&y=${y}&width=${width}&height=${height}&level=${level}`;
export const dziUrl = (slideId: number) => `${API_BASE}/slides/${slideId}/dzi.dzi`;
export const tissueMaskUrl = (slideId: number) => `${API_BASE}/slides/${slideId}/tissue-mask.png`;

// ---- Processing ----
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
export const listSlideAnnotations = (slideId: number) =>
  request<GeometryAnnotation[]>(`/slides/${slideId}/annotations`);
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
export const exportSlideUrl = (slideId: number, formatId: string) => `${API_BASE}/slides/${slideId}/export/${formatId}`;

// ---- Dev ----
export const seedDemoProject = () => request<Project>("/dev/seed-demo", { method: "POST" });
