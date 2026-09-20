export interface ExportFormat {
  id: string;
  name: string;
  desc: string;
  ext: string;
  space: string;
  note: string;
}

export const EXPORT_FORMATS: ExportFormat[] = [
  {
    id: "wsi_json",
    name: "Full WSI JSON",
    desc: "Native hierarchical matrix",
    ext: ".json",
    space: "Level-0 pixels",
    note: "Every annotation with its Level-0 coordinates and the patch it was drawn in. Re-importable into a project.",
  },
  {
    id: "coco",
    name: "COCO Pathology Format",
    desc: "Instance segmentation standard",
    ext: ".json",
    space: "Patch pixels + Level-0",
    note: "Each annotated patch is a COCO image; segmentation, bbox and area are in that patch's own pixels (what training code expects). The Level-0 polygon rides along as vp_level0_segmentation. Points and unclassified shapes are skipped and counted in info.vp_skipped.",
  },
  {
    id: "geojson",
    name: "GeoJSON / Spatial Vectors",
    desc: "Multi-polygon feature collections",
    ext: ".geojson",
    space: "Level-0 pixels",
    note: "A FeatureCollection in Level-0 pixel coordinates (y down, not lon/lat). Property names follow QuPath, so it opens there directly. Self-crossing polygons are kept as drawn and flagged valid_geometry: false.",
  },
  {
    id: "patch_csv",
    name: "Patch-Coordinate CSV",
    desc: "Tabular spatial registry",
    ext: ".csv",
    space: "Level-0 pixels",
    note: "One row per patch of the active config: Level-0 origin and footprint, read level, tissue fraction, review status, annotation count and dominant class. Excluded patches are listed with excluded = true.",
  },
  {
    id: "stats_csv",
    name: "Annotation Statistics CSV",
    desc: "Quantitative summary",
    ext: ".csv",
    space: "px² and mm²",
    note: "One row per class (including classes with no annotations): counts, summed and mean area in px² and mm², share of annotated and tissue area. Areas are summed per annotation, so overlapping shapes count twice.",
  },
];
