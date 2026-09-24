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

/** A patch grid: the patch size, stride, magnification and tissue threshold patches are cut with. */
export interface GridSpec {
  patch_width: number;
  patch_height: number;
  stride_x: number;
  stride_y: number;
  target_magnification: number | null;
  min_tissue_fraction: number;
  include_edge_patches: boolean;
  allow_partial_patches: boolean;
}

/** A grid a slide has (or its configuration's own grid, which it may not have generated yet). */
export interface PatchGrid {
  key: string;
  label: string;
  spec: GridSpec;
  patch_count: number;
  annotated_patch_count: number;
  active: boolean;
  is_default: boolean;
}

export type TissueSource = "auto" | "manual";
export type TissueRegionMode = "add" | "remove";
export type TissueRegionType = "rectangle" | "polygon" | "freehand" | "circle";

/** A hand-drawn area that adds tissue to the slide's mask or removes it, in Level-0 pixels. */
export interface TissueRegion {
  id: number;
  mode: TissueRegionMode;
  type: TissueRegionType;
  coordinates: [number, number][];
}

export interface TissueRegions {
  source: TissueSource;
  regions: TissueRegion[];
  tissue_area_mm2: number | null;
  tissue_coverage_pct: number | null;
  has_mask: boolean;
}

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
  source_type: "upload" | "path";
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
  /** Where the tissue mask starts: automatic detection, or empty (only hand-drawn regions). */
  tissue_source: TissueSource;
  /** Pass as `version` to image URLs: lets the browser keep this slide's tiles and patch images. */
  image_version: string;
  /** The patch grid shown and exported (see PatchGrid); null before any patches exist. */
  active_grid_key: string | null;
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

/** Where the patch an annotation belongs to lies on the slide. */
export interface OwnerPatch {
  id: number;
  patch_index: number;
  x: number;
  y: number;
  width: number;
  height: number;
  width_l0: number;
  height_l0: number;
}

/** An annotation drawn in another (overlapping) patch that reaches into the one on screen. */
export interface OverlappingAnnotation {
  annotation: GeometryAnnotation;
  owner: OwnerPatch;
}

export interface GeometryAnnotation {
  id: number;
  /** null: drawn on the whole slide (WSI mode); its coordinates_level0 are then the only coordinates that exist. */
  patch_id: number | null;
  slide_id: number;
  config_version_id: number;
  class_id: number | null;
  type: GeometryType;
  coordinates_patch_local: [number, number][];
  coordinates_level0: [number, number][];
  /** The patch it was drawn in, on the slide: [x0, y0, x1, y1]; null when drawn on the whole slide. */
  patch_bounds_l0?: [number, number, number, number] | null;
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
