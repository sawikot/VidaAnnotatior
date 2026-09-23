import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Button, Card, IconButton, StatusPill } from "../components/primitives";
import { ExportAllMenu } from "../features/export/ExportAllMenu";
import { EditProjectModal } from "../features/projects/EditProjectModal";
import { AddSlideModal } from "../features/slides/AddSlideModal";
import { getProject, listSlides } from "../services/api";
import type { ProjectDetail, Slide } from "../types/api";
import { useContextStore } from "../stores/contextStore";
import { useUiStore } from "../stores/uiStore";

export function ProjectDashboardPage() {
  const { projectId } = useParams();
  const pid = Number(projectId);
  const navigate = useNavigate();
  const pushToast = useUiStore((s) => s.pushToast);
  const setActiveProject = useContextStore((s) => s.setActiveProject);

  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [slides, setSlides] = useState<Slide[]>([]);
  const [addOpen, setAddOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [filterTab, setFilterTab] = useState<"all" | "needs_annotation" | "needs_review" | "completed">("all");

  useEffect(() => {
    setProject(null); // show "Loading..." only when opening a project, never on later refreshes
    refresh();
  }, [pid]);

  // Updates in place. It must not swap the page for a loading state, or the
  // add-slides dialog (and its import report) would be unmounted mid-use.
  function refresh() {
    Promise.all([getProject(pid), listSlides(pid)])
      .then(([p, s]) => {
        setProject(p);
        setSlides(s);
        setActiveProject(p);
      })
      .catch(() => pushToast("Failed to load project", "error"));
  }

  const filteredSlides = slides.filter((s) => {
    if (filterTab === "all") return true;
    if (filterTab === "needs_annotation") return ["imported", "tissue_detected", "patches_generated"].includes(s.status);
    if (filterTab === "needs_review") return s.status === "annotating";
    if (filterTab === "completed") return s.status === "reviewed";
    return true;
  });

  if (!project) {
    return <div className="p-space-xl text-center text-on-surface-variant">Loading...</div>;
  }

  const config = project.active_config;
  const isImage = project.project_type === "image";
  const stats = project.stats;

  return (
    <div className="max-w-[1720px] mx-auto px-gutter sm:px-margin py-space-lg flex flex-col gap-space-lg">
      <div>
        <div className="text-label-sm text-on-surface-variant mb-1">
          <Link to="/projects" className="hover:underline">
            Projects
          </Link>{" "}
          &rsaquo; {project.slug} &rsaquo; Dashboard
        </div>
        <div className="flex items-center justify-between flex-wrap gap-space-sm">
          <div className="flex items-center gap-space-sm">
            <h1 className="font-headline-lg text-headline-lg text-on-surface">{project.name}</h1>
            {config && (
              <span className="px-space-sm py-0.5 rounded-full bg-surface-container-high text-label-sm font-mono">
                {config.version_label}
              </span>
            )}
            <StatusPill status={project.status} />
          </div>
          <div className="flex items-center gap-space-sm">
            {isImage && stats.total_patches > 0 && (
              <Button variant="primary" icon="adjust" onClick={() => navigate(`/projects/${pid}/annotate`)}>
                Annotate
              </Button>
            )}
            <Button icon="add_photo_alternate" onClick={() => setAddOpen(true)}>
              {isImage ? "Add Images" : "Add WSI Slides"}
            </Button>
            <ExportAllMenu
              projectId={pid}
              slideCount={slides.length}
              projectType={project.project_type}
              optionsPath={slides[0] ? `/projects/${pid}/slides/${slides[0].id}/export?scope=project` : undefined}
              disabledReason={
                slides.some((s) => ["patches_generated", "annotating", "reviewed"].includes(s.status))
                  ? undefined
                  : "Process at least one slide (generate patches) before exporting"
              }
            />
            <Button icon="edit" onClick={() => setEditOpen(true)}>
              Edit Details
            </Button>
            <Button icon="account_tree" onClick={() => navigate(`/projects/${pid}/versions`)}>
              Config
            </Button>
          </div>
        </div>
      </div>

      {config && isImage && (
        <Card className="p-space-md bg-surface-container-low flex flex-wrap items-center gap-space-md text-label-md font-mono">
          <ChipKv k="Project Type" v="Images / patches" />
          <ChipKv k="Coordinates" v="image pixels (origin top-left)" />
          <ChipKv k="Classes" v={config.annotation_classes.map((c) => c.name).join(", ") || "none"} />
        </Card>
      )}

      {config && !isImage && (
        <Card className="p-space-md bg-surface-container-low flex flex-wrap items-center gap-space-md text-label-md font-mono">
          <ChipKv k="Tile Matrix" v={`${config.patch_width}x${config.patch_height}`} />
          <ChipKv k="Stride" v={`${config.stride_x}px`} />
          <ChipKv k="Tissue Threshold" v={`>=${Math.round(config.min_tissue_fraction * 100)}%`} />
          <ChipKv k="Target Mag" v={`${config.target_magnification}x`} />
          <ChipKv k="Coordinate Origin" v="L0 (Native Base)" />
          <ChipKv k="Segmentation" v={config.tissue_method} />
        </Card>
      )}

      {/* Metrics */}
      {isImage ? (
        <div className="grid grid-cols-2 xl:grid-cols-5 gap-space-md">
          <Metric label="Total Images" value={stats.total_patches.toLocaleString()} sub={`${stats.slide_count.toLocaleString()} imported`} />
          <Metric label="Annotated" value={stats.annotated_patches.toLocaleString()} sub={pct(stats.annotated_patches, stats.total_patches)} />
          <Metric label="Remaining" value={Math.max(0, stats.total_patches - stats.annotated_patches).toLocaleString()} sub="still to annotate" />
          <Metric label="Reviewed QA" value={stats.reviewed_patches.toLocaleString()} sub={pct(stats.reviewed_patches, stats.total_patches)} />
          <Metric label="Flagged" value={stats.flagged_patches.toLocaleString()} sub="needs attention" />
        </div>
      ) : (
      <div className="grid grid-cols-2 xl:grid-cols-6 gap-space-md">
        <Metric label="Total Slides" value={String(project.stats.slide_count)} sub={`${project.stats.processed_slide_count} processed`} />
        <Metric label="Tissue Extracted" value={`${project.stats.tissue_area_mm2} mm²`} sub="across all slides" />
        <Metric label="Total Patches" value={project.stats.total_patches.toLocaleString()} sub="virtual, coordinate-only" />
        <Metric
          label="Annotated"
          value={project.stats.annotated_patches.toLocaleString()}
          sub={pct(project.stats.annotated_patches, project.stats.total_patches)}
        />
        <Metric
          label="Reviewed QA"
          value={project.stats.reviewed_patches.toLocaleString()}
          sub={pct(project.stats.reviewed_patches, project.stats.total_patches)}
        />
        <Metric label="Flagged" value={project.stats.flagged_patches.toLocaleString()} sub="needs attention" />
      </div>
      )}

      {isImage && (
        <Card className="p-space-lg flex flex-col gap-space-md">
          <div className="flex items-center justify-between flex-wrap gap-space-sm">
            <div>
              <div className="font-headline-md text-headline-md">Images</div>
              <div className="text-body-sm text-on-surface-variant">
                Each image is annotated as it is, with coordinates in its own pixels. Browse them all, or jump straight in.
              </div>
            </div>
            <div className="flex items-center gap-space-sm">
              <Button icon="grid_on" onClick={() => navigate(`/projects/${pid}/images`)} disabled={stats.total_patches === 0}>
                Browse images
              </Button>
              <Button variant="primary" icon="adjust" onClick={() => navigate(`/projects/${pid}/annotate`)} disabled={stats.total_patches === 0}>
                {stats.annotated_patches > 0 ? "Continue annotating" : "Start annotating"}
              </Button>
            </div>
          </div>
          <div className="h-2 rounded-full bg-surface-container-high overflow-hidden" aria-label="Annotation progress">
            <div
              className="h-full bg-primary"
              style={{ width: `${stats.total_patches ? Math.min(100, (stats.annotated_patches / stats.total_patches) * 100) : 0}%` }}
            />
          </div>
          {stats.total_patches === 0 && (
            <div className="text-body-md text-on-surface-variant">
              No images yet. Click "Add Images" to upload images, a folder, or a .zip of them.
            </div>
          )}
        </Card>
      )}

      {/* Slide table */}
      {!isImage && (
      <Card>
        <div className="flex items-center gap-space-sm p-space-sm border-b border-outline-variant flex-wrap">
          {(
            [
              ["all", `All Slides (${slides.length})`],
              ["needs_annotation", "Needs Annotation"],
              ["needs_review", "Needs Review"],
              ["completed", "Completed"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setFilterTab(key)}
              className={`px-space-md py-1.5 rounded-full text-label-md ${
                filterTab === key ? "bg-primary text-on-primary" : "bg-surface-container-low text-on-surface-variant"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {filteredSlides.length === 0 ? (
          <div className="p-space-xl text-center text-on-surface-variant">
            No slides yet. Click "Add WSI Slides" to import a .svs/.tif/.tiff/.ndpi file.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-body-md">
              <thead className="bg-surface-container-low text-label-md text-on-surface-variant">
                <tr>
                  <th className="text-left px-space-md py-space-sm">Slide</th>
                  <th className="text-left px-space-md py-space-sm">Dimensions (L0)</th>
                  <th className="text-left px-space-md py-space-sm">Tissue %</th>
                  <th className="text-left px-space-md py-space-sm">Status</th>
                  <th className="text-left px-space-md py-space-sm">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredSlides.map((s) => (
                  <SlideRow key={s.id} slide={s} projectId={pid} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      )}

      <EditProjectModal
        open={editOpen}
        onClose={() => setEditOpen(false)}
        project={project}
        onSaved={(p) => {
          setEditOpen(false);
          setProject(p);
          setActiveProject(p);
        }}
      />

      <AddSlideModal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        projectId={pid}
        projectType={project.project_type}
        configVersionId={project.active_config_version_id}
        onImported={refresh}
      />
    </div>
  );
}

function pct(n: number, total: number) {
  if (!total) return "0%";
  return `${Math.round((n / total) * 100)}%`;
}

function ChipKv({ k, v }: { k: string; v: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="text-on-surface-variant">{k}:</span>
      <span className="text-on-surface">{v}</span>
    </span>
  );
}

function Metric({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <Card className="p-space-md">
      <div className="text-label-md text-on-surface-variant">{label}</div>
      <div className="font-headline-lg text-headline-lg text-on-surface">{value}</div>
      <div className="text-body-sm text-on-surface-variant">{sub}</div>
    </Card>
  );
}

function SlideRow({ slide, projectId }: { slide: Slide; projectId: number }) {
  const navigate = useNavigate();
  const canAnnotate = ["patches_generated", "annotating", "reviewed"].includes(slide.status);
  return (
    <tr className="border-t border-outline-variant hover:bg-surface-container-low/50">
      <td className="px-space-md py-space-sm">
        <div className="font-headline-sm text-headline-sm">{slide.filename}</div>
        <div className="text-label-sm text-secondary font-mono">{slide.format}</div>
      </td>
      <td className="px-space-md py-space-sm font-mono text-label-md">
        {slide.width_l0 ? `${slide.width_l0.toLocaleString()} x ${slide.height_l0?.toLocaleString()}` : "--"}
      </td>
      <td className="px-space-md py-space-sm font-mono text-label-md">
        {slide.tissue_coverage_pct != null ? `${slide.tissue_coverage_pct}%` : "--"}
      </td>
      <td className="px-space-md py-space-sm">
        <StatusPill status={slide.status} />
        {slide.status === "error" && <div className="text-body-sm text-error mt-1">{slide.error_message}</div>}
      </td>
      <td className="px-space-md py-space-sm">
        <div className="flex items-center gap-1.5">
          <Button variant="secondary" onClick={() => navigate(`/projects/${projectId}/slides/${slide.id}/processing`)}>
            Process
          </Button>
          {canAnnotate && (
            <Button variant="primary" onClick={() => navigate(`/projects/${projectId}/slides/${slide.id}/workspace`)}>
              Annotate
            </Button>
          )}
          {canAnnotate && (
            <IconButton
              icon="file_download"
              onClick={() => navigate(`/projects/${projectId}/slides/${slide.id}/export`)}
              title="Export this slide's annotations"
            />
          )}
        </div>
      </td>
    </tr>
  );
}
