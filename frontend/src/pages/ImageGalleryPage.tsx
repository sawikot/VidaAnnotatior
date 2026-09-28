import { useEffect, useMemo, useState } from "react";
import { useCan } from "../stores/authStore";
import { useNavigate, useParams } from "react-router-dom";
import { MaterialIcon } from "../components/MaterialIcon";
import { Pagination } from "../components/Pagination";
import { Button, Card, StatusPill } from "../components/primitives";
import { AddSlideModal } from "../features/slides/AddSlideModal";
import { DeleteSlideModal, type DeleteTarget } from "../features/slides/DeleteSlideModal";
import { getProject, listImages, thumbnailUrl } from "../services/api";
import { useContextStore } from "../stores/contextStore";
import type { ImageSummary, PatchStatus, ProjectDetail } from "../types/api";
import { usePageParams } from "../utils/pagination";

type FilterKey = "all" | PatchStatus | "flagged";

/** Every image of an image project as a filterable thumbnail grid. */
export function ImageGalleryPage() {
  const canManage = useCan().manage;
  const { projectId } = useParams();
  const pid = Number(projectId);
  const navigate = useNavigate();
  const setActiveProject = useContextStore((s) => s.setActiveProject);

  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [images, setImages] = useState<ImageSummary[] | null>(null);
  const [filter, setFilter] = useState<FilterKey>("all");
  const [query, setQuery] = useState("");
  const { page, pageSize, setPage, setPageSize } = usePageParams(48);
  // The page controls are at the bottom: start each new page at the top.
  useEffect(() => {
    window.scrollTo({ top: 0 }); // in braces: newer browsers return a Promise here, and an effect may only return a cleanup
  }, [page, pageSize]);
  const [addOpen, setAddOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null);

  function load() {
    getProject(pid).then((p) => {
      setProject(p);
      setActiveProject(p);
    });
    listImages(pid).then((r) => setImages(r.items));
  }
  useEffect(load, [pid]);

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: 0, flagged: 0 };
    for (const i of images ?? []) {
      c.all += 1;
      c[i.status] = (c[i.status] ?? 0) + 1;
      if (i.flagged) c.flagged += 1;
    }
    return c;
  }, [images]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (images ?? []).filter((i) => {
      if (filter === "flagged" ? !i.flagged : filter !== "all" && i.status !== filter) return false;
      return !q || i.filename.toLowerCase().includes(q);
    });
  }, [images, filter, query]);

  if (!project || !images) return <div className="p-space-xl text-center text-on-surface-variant">Loading...</div>;

  const shownPage = Math.min(page, Math.max(0, Math.ceil(visible.length / pageSize) - 1)); // a page past the end shows the last
  const shown = visible.slice(shownPage * pageSize, (shownPage + 1) * pageSize);
  const pills: { key: FilterKey; label: string }[] = [
    { key: "all", label: `All (${counts.all})` },
    { key: "unannotated", label: `To do (${counts.unannotated ?? 0})` },
    { key: "annotated", label: `Annotated (${counts.annotated ?? 0})` },
    { key: "reviewed", label: `Reviewed (${counts.reviewed ?? 0})` },
    { key: "skipped", label: `Skipped (${counts.skipped ?? 0})` },
    { key: "flagged", label: `Flagged (${counts.flagged})` },
  ];

  return (
    <div className="max-w-[1720px] mx-auto px-gutter sm:px-margin py-space-lg flex flex-col gap-space-md">
      <Card className="p-space-sm sticky top-14 z-20 flex flex-col gap-space-sm">
        <div className="flex items-center justify-between flex-wrap gap-space-sm">
          <div className="flex items-center gap-space-sm">
            <span className="font-headline-md text-headline-md">{project.name}</span>
            <span className="px-space-sm py-0.5 rounded-full bg-surface-container-high text-label-sm font-mono">
              {counts.all.toLocaleString()} images
            </span>
          </div>
          <div className="flex items-center gap-space-sm">
            <input
              className="input w-56"
              placeholder="Search file name..."
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setPage(0);
              }}
            />
            {canManage && (
              <Button icon="add_photo_alternate" onClick={() => setAddOpen(true)}>
                Add Images
              </Button>
            )}
          </div>
        </div>
        <div className="flex items-center gap-space-sm flex-wrap">
          {pills.map((f) => (
            <button
              key={f.key}
              onClick={() => {
                setFilter(f.key);
                setPage(0);
              }}
              className={`px-space-sm py-1 rounded-full text-label-sm ${
                filter === f.key ? "bg-primary text-on-primary" : "bg-surface-container-low text-on-surface-variant"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </Card>

      {shown.length === 0 ? (
        <Card className="p-space-xl text-center text-on-surface-variant">
          {counts.all === 0 ? 'No images yet. Click "Add Images" to import some.' : "No images match this filter."}
        </Card>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-space-md">
          {shown.map((img) => (
            <div
              key={img.slide_id}
              className="group relative flex flex-col bg-surface-container-lowest rounded-xl overflow-hidden shadow-sm hover:shadow-md transition-all cursor-pointer"
              onClick={() => navigate(`/projects/${pid}/slides/${img.slide_id}/workspace`)}
            >
              <div className="relative aspect-square w-full bg-surface-dim overflow-hidden">
                <img
                  src={thumbnailUrl(img.slide_id, 256)}
                  alt={img.filename}
                  loading="lazy"
                  className="w-full h-full object-contain group-hover:scale-105 transition-transform duration-300"
                />
                <div className="absolute top-1.5 right-1.5">
                  <StatusPill status={img.status} />
                </div>
                {canManage && (
                  <button
                    title="Delete this image and its annotations"
                    aria-label={`Delete ${img.filename}`}
                    className="absolute top-1.5 left-1.5 z-10 w-7 h-7 rounded-full bg-surface-container-lowest/90 text-error shadow-sm flex items-center justify-center opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity"
                    onClick={(e) => {
                      e.stopPropagation(); // the card itself opens the image
                      setDeleteTarget({
                        id: img.slide_id,
                        filename: img.filename,
                        detail: img.annotation_count ? `${img.annotation_count} object${img.annotation_count === 1 ? "" : "s"}` : undefined,
                      });
                    }}
                  >
                    <MaterialIcon name="delete" className="!text-[16px]" />
                  </button>
                )}
                <div className="absolute inset-0 bg-on-background/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
                  <span className="px-space-md py-1.5 rounded bg-primary text-on-primary text-label-md flex items-center gap-1">
                    <MaterialIcon name="edit" className="!text-[16px]" />
                    Annotate
                  </span>
                </div>
              </div>
              <div className="p-space-sm flex flex-col gap-0.5 text-label-sm">
                <span className="font-mono truncate" title={img.filename}>
                  {img.filename}
                </span>
                <span className="flex items-center justify-between text-on-surface-variant">
                  <span>
                    {img.width}×{img.height} · {img.annotation_count} object{img.annotation_count === 1 ? "" : "s"}
                  </span>
                  {img.flagged && <MaterialIcon name="flag" className="!text-[14px] text-error" />}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      <Pagination page={shownPage} pageSize={pageSize} total={visible.length} onPage={setPage} onPageSize={setPageSize} noun="images" />

      <DeleteSlideModal target={deleteTarget} noun="image" onClose={() => setDeleteTarget(null)} onDeleted={load} />

      <AddSlideModal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        projectId={pid}
        projectType="image"
        configVersionId={project.active_config_version_id}
        onImported={load}
      />
    </div>
  );
}
