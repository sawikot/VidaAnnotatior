import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { MaterialIcon } from "../components/MaterialIcon";
import { Button, Card, Modal, StatusPill } from "../components/primitives";
import {
  createDemoSlide,
  getProject,
  importSlideByPath,
  listSlides,
  uploadSlide,
} from "../services/api";
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
  const [loading, setLoading] = useState(true);
  const [addOpen, setAddOpen] = useState(false);
  const [filterTab, setFilterTab] = useState<"all" | "needs_annotation" | "needs_review" | "completed">("all");

  useEffect(() => {
    refresh();
    return () => setActiveProject(null);
  }, [pid]);

  function refresh() {
    setLoading(true);
    Promise.all([getProject(pid), listSlides(pid)])
      .then(([p, s]) => {
        setProject(p);
        setSlides(s);
        setActiveProject(p);
      })
      .catch(() => pushToast("Failed to load project", "error"))
      .finally(() => setLoading(false));
  }

  const filteredSlides = slides.filter((s) => {
    if (filterTab === "all") return true;
    if (filterTab === "needs_annotation") return ["imported", "tissue_detected", "patches_generated"].includes(s.status);
    if (filterTab === "needs_review") return s.status === "annotating";
    if (filterTab === "completed") return s.status === "reviewed";
    return true;
  });

  if (loading || !project) {
    return <div className="p-space-xl text-center text-on-surface-variant">Loading...</div>;
  }

  const config = project.active_config;

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
            <Button icon="add_photo_alternate" onClick={() => setAddOpen(true)}>
              Add WSI Slides
            </Button>
            <Button icon="account_tree" onClick={() => navigate(`/projects/${pid}/versions`)}>
              Config
            </Button>
          </div>
        </div>
      </div>

      {config && (
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

      {/* Slide table */}
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
            No slides yet. Click "Add WSI Slides" to import a .svs/.tif/.tiff/.ndpi file, or add a synthetic demo
            slide.
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

      <AddSlideModal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        projectId={pid}
        configVersionId={project.active_config_version_id}
        onImported={() => {
          setAddOpen(false);
          refresh();
        }}
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
        </div>
      </td>
    </tr>
  );
}

function AddSlideModal({
  open,
  onClose,
  projectId,
  configVersionId,
  onImported,
}: {
  open: boolean;
  onClose: () => void;
  projectId: number;
  configVersionId: number | null;
  onImported: () => void;
}) {
  const pushToast = useUiStore((s) => s.pushToast);
  const [mode, setMode] = useState<"upload" | "path" | "demo">("upload");
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleUpload(file: File) {
    setBusy(true);
    try {
      await uploadSlide(projectId, file, configVersionId ?? undefined);
      pushToast(`${file.name} imported`, "success");
      onImported();
    } catch (e) {
      pushToast(e instanceof Error ? e.message : "Upload failed", "error");
    } finally {
      setBusy(false);
    }
  }

  async function handlePathImport() {
    setBusy(true);
    try {
      await importSlideByPath(projectId, path, configVersionId ?? undefined);
      pushToast("Slide registered", "success");
      onImported();
    } catch (e) {
      pushToast(e instanceof Error ? e.message : "Import failed", "error");
    } finally {
      setBusy(false);
    }
  }

  async function handleDemo() {
    setBusy(true);
    try {
      await createDemoSlide(projectId, undefined, configVersionId ?? undefined);
      pushToast("Demo slide added", "success");
      onImported();
    } catch (e) {
      pushToast(e instanceof Error ? e.message : "Failed to add demo slide", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose}>
      <div className="p-space-lg flex flex-col gap-space-md">
        <div className="flex items-center justify-between">
          <h2 className="font-headline-md text-headline-md">Add WSI Slide</h2>
          <button onClick={onClose}>
            <MaterialIcon name="close" />
          </button>
        </div>
        <div className="flex bg-surface-container-low rounded p-0.5">
          {(["upload", "path", "demo"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`flex-1 py-1.5 rounded text-label-md capitalize ${mode === m ? "bg-surface-container-lowest shadow-sm" : ""}`}
            >
              {m === "path" ? "Server Path" : m}
            </button>
          ))}
        </div>

        {mode === "upload" && (
          <div>
            <p className="text-body-md text-on-surface-variant mb-space-sm">
              Upload a .svs, .tif, .tiff, or .ndpi file. Larger files may take a moment.
            </p>
            <input
              type="file"
              accept=".svs,.tif,.tiff,.ndpi"
              disabled={busy}
              onChange={(e) => e.target.files?.[0] && handleUpload(e.target.files[0])}
              className="text-body-md"
            />
          </div>
        )}

        {mode === "path" && (
          <div className="flex flex-col gap-space-sm">
            <p className="text-body-md text-on-surface-variant">
              Register a file already under the server's configured WSI watch directory (WSI_WATCH_DIR).
            </p>
            <input
              className="input"
              placeholder="C:/path/to/watch-dir/slide.svs"
              value={path}
              onChange={(e) => setPath(e.target.value)}
            />
            <Button variant="primary" disabled={busy || !path} onClick={handlePathImport}>
              {busy ? "Importing..." : "Register Slide"}
            </Button>
          </div>
        )}

        {mode === "demo" && (
          <div className="flex flex-col gap-space-sm">
            <p className="text-body-md text-on-surface-variant">
              Add a synthetic, procedurally generated slide -- no real WSI file needed. Useful for exercising the
              full pipeline (tissue detection, patch generation, annotation, export) without patient data.
            </p>
            <Button variant="primary" disabled={busy} onClick={handleDemo}>
              {busy ? "Generating..." : "Add Demo Slide"}
            </Button>
          </div>
        )}
      </div>
    </Modal>
  );
}
