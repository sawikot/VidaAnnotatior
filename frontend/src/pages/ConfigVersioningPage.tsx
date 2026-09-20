import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { MaterialIcon } from "../components/MaterialIcon";
import { Button, Card } from "../components/primitives";
import { EditConfigModal } from "../features/projects/EditConfigModal";
import { getConfigUsage, getProject, listConfigs, lockConfig, updateProject } from "../services/api";
import { useUiStore } from "../stores/uiStore";
import type { ConfigUsage, ConfigVersion, ProjectDetail } from "../types/api";

interface EditTarget {
  config: ConfigVersion;
  mode: "edit" | "fork";
}

export function ConfigVersioningPage() {
  const { projectId } = useParams();
  const pid = Number(projectId);
  const pushToast = useUiStore((s) => s.pushToast);

  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [configs, setConfigs] = useState<ConfigVersion[]>([]);
  const [usage, setUsage] = useState<Record<number, ConfigUsage>>({});
  const [target, setTarget] = useState<EditTarget | null>(null);

  const refresh = useCallback(async () => {
    const [p, c] = await Promise.all([getProject(pid), listConfigs(pid)]);
    setProject(p);
    setConfigs(c);
    const entries = await Promise.all(
      c.map((cfg) => getConfigUsage(cfg.id).then((u) => [cfg.id, u] as const).catch(() => null)),
    );
    setUsage(Object.fromEntries(entries.filter((e): e is [number, ConfigUsage] => e !== null)));
  }, [pid]);

  useEffect(() => {
    refresh().catch(() => pushToast("Failed to load configuration versions", "error"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  async function handleLock(id: number) {
    try {
      await lockConfig(id);
      pushToast("Version locked", "success");
      await refresh();
    } catch {
      pushToast("Failed to lock version", "error");
    }
  }

  async function handleMakeDefault(id: number) {
    try {
      await updateProject(pid, { active_config_version_id: id });
      pushToast("New slides will now start on this version", "success");
      await refresh();
    } catch {
      pushToast("Failed to change the default version", "error");
    }
  }

  if (!project) return <div className="p-space-xl text-center text-on-surface-variant">Loading...</div>;
  const isImage = project.project_type === "image";

  return (
    <div className="max-w-6xl mx-auto px-gutter sm:px-margin py-space-lg flex flex-col gap-space-lg">
      <div>
        <div className="text-label-sm text-on-surface-variant mb-1">
          <Link to="/projects" className="hover:underline">
            Projects
          </Link>{" "}
          &rsaquo; {project.slug} &rsaquo; Configuration Versioning
        </div>
        <div className="flex items-center justify-between flex-wrap gap-space-sm">
          <h1 className="font-headline-lg text-headline-lg">Configuration Versioning & Reproducibility Audit</h1>
          {project.active_config && !isImage && (
            <Button variant="primary" icon="account_tree" onClick={() => setTarget({ config: project.active_config!, mode: "fork" })}>
              Create New Config Version
            </Button>
          )}
        </div>
      </div>

      <Card className="p-space-md bg-surface-container-low border-l-4 border-error flex items-start gap-space-sm">
        <MaterialIcon name="gavel" className="text-error shrink-0" />
        <span className="text-body-md">
          {isImage ? (
            <>
              An image project has a single configuration: each image is annotated as it is, so there is no patch grid to
              version. Classes, tools and QC settings can be edited at any time; annotations keep pointing at their class
              when you rename or recolor it.
            </>
          ) : (
            <>
              Patch size, stride, magnification and tissue threshold define where every patch and annotation sits, so they
              can't be changed in place once a version has generated patches or is locked -- editing them creates a new
              version instead. Classes, tools, QC settings and tissue-detection defaults can always be edited. To run an
              existing slide on another version, pick it in the slide's <em>Config version</em> selector on Slide Processing.
            </>
          )}
        </span>
      </Card>

      <div className="flex flex-col gap-space-md">
        {configs.map((c) => (
          <ConfigCard
            key={c.id}
            config={c}
            usage={usage[c.id]}
            isDefault={c.id === project.active_config_version_id}
            onEdit={() => setTarget({ config: c, mode: "edit" })}
            onFork={isImage ? undefined : () => setTarget({ config: c, mode: "fork" })}
            isImage={isImage}
            onLock={() => handleLock(c.id)}
            onMakeDefault={() => handleMakeDefault(c.id)}
          />
        ))}
      </div>

      <EditConfigModal
        open={!!target}
        onClose={() => setTarget(null)}
        config={target?.config ?? null}
        mode={target?.mode ?? "edit"}
        projectType={project.project_type}
        existingLabels={configs.map((c) => c.version_label)}
        onSaved={() => {
          setTarget(null);
          refresh();
        }}
      />
    </div>
  );
}

function ConfigCard({
  config,
  usage,
  isDefault,
  onEdit,
  onFork,
  isImage = false,
  onLock,
  onMakeDefault,
}: {
  config: ConfigVersion;
  usage?: ConfigUsage;
  isDefault: boolean;
  onEdit: () => void;
  onFork?: () => void;
  isImage?: boolean;
  onLock: () => void;
  onMakeDefault: () => void;
}) {
  return (
    <Card className={`p-space-lg ${isDefault ? "ring-1 ring-primary" : ""}`}>
      <div className="flex items-center justify-between flex-wrap gap-space-sm mb-space-md">
        <div className="flex items-center gap-space-sm flex-wrap">
          <span className="px-space-sm py-0.5 rounded-full bg-surface-container-high font-mono text-label-md">{config.version_label}</span>
          <h3 className="font-headline-md text-headline-md">{config.title || "Untitled configuration"}</h3>
          <span
            className={`px-space-sm py-0.5 rounded-full text-label-sm capitalize ${
              config.status === "locked" ? "bg-tertiary-fixed text-on-tertiary-fixed-variant" : "bg-surface-container-high text-on-surface-variant"
            }`}
          >
            {config.status === "locked" ? "Locked & Certified" : config.status}
          </span>
          {isDefault && <span className="px-space-sm py-0.5 rounded-full text-label-sm bg-primary text-on-primary">Default for new slides</span>}
        </div>
        <div className="flex items-center gap-space-sm flex-wrap">
          {!isDefault && (
            <Button variant="ghost" icon="check_circle" onClick={onMakeDefault} title="New slides will start on this version">
              Use for new slides
            </Button>
          )}
          {config.status !== "locked" && (
            <Button variant="ghost" icon="lock" onClick={onLock}>
              Lock
            </Button>
          )}
          {onFork && (
            <Button variant="secondary" icon="call_split" onClick={onFork}>
              New version
            </Button>
          )}
          <Button variant="primary" icon="edit" onClick={onEdit}>
            Edit
          </Button>
        </div>
      </div>

      {!isImage && (
      <div className="grid grid-cols-2 md:grid-cols-4 gap-space-md text-label-md">
        <Kv label="Patch dimension" value={`${config.patch_width}x${config.patch_height}`} />
        <Kv label="Stride" value={config.stride_x === config.stride_y ? `${config.stride_x}px` : `${config.stride_x} x ${config.stride_y}px`} />
        <Kv label="Magnification" value={`${config.target_magnification}x`} />
        <Kv label="Tissue threshold" value={`>=${Math.round(config.min_tissue_fraction * 100)}%`} />
      </div>
      )}

      <div className="mt-space-md flex flex-wrap gap-x-space-md gap-y-1.5 items-center">
        {config.annotation_classes.map((k) => (
          <span key={k.id} className="inline-flex items-center gap-1.5 text-label-sm">
            <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: k.color_hex }} />
            {k.name}
            {k.hotkey && <span className="font-mono text-on-surface-variant">[{k.hotkey}]</span>}
          </span>
        ))}
      </div>

      <div className="mt-space-md pt-space-sm border-t border-outline-variant flex flex-wrap items-center gap-x-space-lg gap-y-1 text-label-sm text-on-surface-variant font-mono">
        <span className="inline-flex items-center gap-1">
          <MaterialIcon name="fingerprint" className="!text-[14px]" />
          {config.config_hash?.slice(0, 16)}...
        </span>
        {usage ? (
          <>
            <span>{isImage ? count(usage.patch_count, "image", "images") : count(usage.patch_count, "patch", "patches")}</span>
            <span>{count(usage.annotation_count, "annotation", "annotations")}</span>
            {!isImage && <span>{count(usage.slide_count, "slide", "slides")} using it</span>}
          </>
        ) : (
          <span>usage unavailable</span>
        )}
        <span>{isImage ? "Coordinate frame: image pixels" : "Coordinate frame: Level-0 absolute (px)"}</span>
      </div>
    </Card>
  );
}

function count(n: number, one: string, many: string) {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

function Kv({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-on-surface-variant text-label-sm">{label}</div>
      <div className="font-mono">{value}</div>
    </div>
  );
}
