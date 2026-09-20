export type ProjectStatus = "active" | "review" | "completed";
/** "wsi": gigapixel slides tiled into virtual patches. "image": ordinary images annotated as they are. */
export type ProjectType = "wsi" | "image";
export type SlideStatus =
  | "imported"
  | "tissue_detected"
  | "patches_generated"
  | "annotating"
  | "reviewed"
  | "error";
export type PatchStatus = "unannotated" | "active" | "annotated" | "reviewed" | "skipped" | "flagged";
/** point, line, freehand_line (open path), rectangle, circle (centre + edge point), polygon, freehand (closed outline). */
export type GeometryType = "point" | "line" | "freehand_line" | "rectangle" | "circle" | "polygon" | "freehand";
export type ConfigStatus = "draft" | "locked" | "experimental" | "deprecated";

export interface AnnotationClass {
  id: number;
  name: string;
  color_hex: string;
  hotkey: string | null;
  order_index: number;
}

export interface ConfigVersion {
  id: number;
  project_id: number;
  parent_version_id: number | null;
  version_label: string;
  status: ConfigStatus;
  title: string | null;
  created_by: string | null;
  config_hash: string | null;

  coordinate_system: string;
  target_magnification: number | null;
  target_level: number | null;
  mpp_handling: string;

  patch_width: number;
  patch_height: number;
  stride_x: number;
  stride_y: number;
  min_tissue_fraction: number;
  allow_partial_patches: boolean;
  include_edge_patches: boolean;

  tissue_method: string;
  tissue_params: Record<string, unknown>;
  enabled_tools: string[];

  allow_skip: boolean;
  allow_unsure: boolean;
  require_annotation: boolean;
  reviewer_mode: boolean;

  created_at: string;
  updated_at: string;
  annotation_classes: AnnotationClass[];
}

export interface ProjectStats {
  slide_count: number;
  processed_slide_count: number;
  total_patches: number;
  annotated_patches: number;
  reviewed_patches: number;
  flagged_patches: number;
  tissue_area_mm2: number;
}

export interface Project {
  id: number;
  slug: string;
  name: string;
  organ: string | null;
  description: string | null;
  team: string | null;
  status: ProjectStatus;
  project_type: ProjectType;
  active_config_version_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectDetail extends Project {
  stats: ProjectStats;
  active_config: ConfigVersion | null;
}

export interface Slide {
  id: number;
  project_id: number;
  filename: string;
  source_type: "upload" | "path" | "demo";
  format: string | null;
  status: SlideStatus;
  error_message: string | null;

  width_l0: number | null;
  height_l0: number | null;
  level_count: number | null;
  level_dimensions: [number, number][] | null;
  level_downsamples: number[] | null;
  mpp_x: number | null;
  mpp_y: number | null;
  magnification: number | null;

  tissue_area_mm2: number | null;
  tissue_coverage_pct: number | null;
  tissue_mask_path: string | null;
  active_config_version_id: number | null;

  created_at: string;
  updated_at: string;
}

export interface Patch {
  id: number;
  slide_id: number;
  config_version_id: number;
  patch_index: number;
  x: number;
  y: number;
  level: number;
  width: number;
  height: number;
  width_l0: number;
  height_l0: number;
  tissue_fraction: number;
  status: PatchStatus;
  patch_label: string | null;
  unsure: boolean;
  flagged: boolean;
  excluded: boolean;
  notes: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface PatchListResponse {
  total: number;
  items: Patch[];
}

export interface GeometryAnnotation {
  id: number;
  patch_id: number;
  slide_id: number;
  config_version_id: number;
  class_id: number | null;
  type: GeometryType;
  coordinates_patch_local: [number, number][];
  coordinates_level0: [number, number][];
  created_by: string | null;
  notes: string | null;
  unsure: boolean;
  flagged: boolean;
  excluded: boolean;
  created_at: string;
  updated_at: string;
}

export interface ConfigUsage {
  patch_count: number;
  annotation_count: number;
  slide_count: number;
}

export interface SlideBatchImportResult {
  slides: Slide[];
  skipped: { name: string; reason: string }[];
  ignored_file_count: number;
  warnings: string[];
}

export interface WsiFormats {
  formats: { extension: string; description: string }[];
  archives: string[];
  max_upload_bytes: number;
  max_upload_files: number;
}

/** One image of an image project with the state of its single patch. */
export interface ImageSummary {
  slide_id: number;
  patch_id: number;
  filename: string;
  width: number;
  height: number;
  status: PatchStatus;
  unsure: boolean;
  flagged: boolean;
  excluded: boolean;
  annotation_count: number;
}

export interface ImageList {
  total: number;
  items: ImageSummary[];
}
