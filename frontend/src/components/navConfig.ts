import type { ProjectType } from "../types/api";

export interface NavItem {
  key: string;
  label: string;
  icon: string;
  path: (projectId?: number, slideId?: number) => string;
  requiresProjectSlide?: boolean;
  requiresProject?: boolean;
}

export const NAV_ITEMS: NavItem[] = [
  { key: "projects", label: "Projects", icon: "folder", path: () => "/projects" },
  {
    key: "dashboard",
    label: "Dashboard",
    icon: "grid_view",
    path: (p) => (p ? `/projects/${p}` : "/projects"),
    requiresProject: true,
  },
  {
    key: "slide-processing",
    label: "Slide Processing",
    icon: "filter_center_focus",
    path: (p, s) => (p && s ? `/projects/${p}/slides/${s}/processing` : "/projects"),
    requiresProjectSlide: true,
  },
  {
    key: "workspace-annotator",
    label: "Workspace",
    icon: "adjust",
    path: (p, s) => (p && s ? `/projects/${p}/slides/${s}/workspace` : "/projects"),
    requiresProjectSlide: true,
  },
  {
    key: "patch-gallery",
    label: "Patch Gallery",
    icon: "grid_on",
    path: (p, s) => (p && s ? `/projects/${p}/slides/${s}/gallery` : "/projects"),
    requiresProjectSlide: true,
  },
  {
    key: "full-wsi-overview",
    label: "Overview",
    icon: "aspect_ratio",
    path: (p, s) => (p && s ? `/projects/${p}/slides/${s}/overview` : "/projects"),
    requiresProjectSlide: true,
  },
  {
    key: "versioning",
    label: "Versioning",
    icon: "account_tree",
    path: (p) => (p ? `/projects/${p}/versions` : "/projects"),
    requiresProject: true,
  },
  {
    key: "export",
    label: "Export",
    icon: "file_download",
    path: (p, s) => (p && s ? `/projects/${p}/slides/${s}/export` : p ? `/projects/${p}` : "/projects"),
    requiresProject: true,
  },
];

/** Image projects have no tissue detection, patch grid or stitched overview: an image is
 * annotated directly, and the gallery lists the images themselves. */
const IMAGE_NAV_ITEMS: NavItem[] = [
  NAV_ITEMS[0],
  NAV_ITEMS[1],
  {
    key: "workspace-annotator",
    label: "Annotate",
    icon: "adjust",
    // Inside an image: stay on it. Elsewhere: open the first image still to do.
    path: (p, s) => (p && s ? `/projects/${p}/slides/${s}/workspace` : p ? `/projects/${p}/annotate` : "/projects"),
    requiresProject: true,
  },
  {
    key: "image-gallery",
    label: "Images",
    icon: "grid_on",
    path: (p) => (p ? `/projects/${p}/images` : "/projects"),
    requiresProject: true,
  },
  NAV_ITEMS[6],
  NAV_ITEMS[7],
];

export function navItemsFor(projectType: ProjectType | undefined): NavItem[] {
  return projectType === "image" ? IMAGE_NAV_ITEMS : NAV_ITEMS;
}
