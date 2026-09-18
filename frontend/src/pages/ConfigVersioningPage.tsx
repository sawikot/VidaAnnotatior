import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { MaterialIcon } from "../components/MaterialIcon";
import { Button, Card, Modal } from "../components/primitives";
import { forkConfig, getProject, listConfigs, lockConfig } from "../services/api";
import type { ConfigVersion, ProjectDetail } from "../types/api";
import { useUiStore } from "../stores/uiStore";

export function ConfigVersioningPage() {
  const { projectId } = useParams();
  const pid = Number(projectId);
  const pushToast = useUiStore((s) => s.pushToast);

  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [configs, setConfigs] = useState<ConfigVersion[]>([]);
  const [forkOpen, setForkOpen] = useState(false);
  const [forkSource, setForkSource] = useState<ConfigVersion | null>(null);

  useEffect(() => {
    refresh();
  }, [pid]);

  function refresh() {
    Promise.all([getProject(pid), listConfigs(pid)]).then(([p, c]) => {
      setProject(p);
      setConfigs(c);
    });
  }

  async function handleLock(id: number) {
    try {
      await lockConfig(id);
      pushToast("Version locked", "success");
      refresh();
    } catch {
      pushToast("Failed to lock version", "error");
    }
  }

  if (!project) return <div className="p-space-xl text-center text-on-surface-variant">Loading...</div>;

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
          <Button
            variant="primary"
            icon="account_tree"
            onClick={() => {
              setForkSource(project.active_config);
              setForkOpen(true);
            }}
          >
            Create New Config Version
          </Button>
        </div>
      </div>

      <Card className="p-space-md bg-surface-container-low border-l-4 border-error flex items-center justify-between flex-wrap gap-space-sm">
        <div className="flex items-center gap-space-sm">
          <MaterialIcon name="gavel" className="text-error" />
          <span className="text-body-md">
            Critical parameters (patch size, stride, level, magnification, tissue threshold) are immutable once a
            version has generated patches. Changing them requires forking a new version.
          </span>
        </div>
      </Card>

      <div className="flex flex-col gap-space-md">
        {configs.map((c) => (
          <ConfigCard
            key={c.id}
            config={c}
            isActive={c.id === project.active_config_version_id}
            onLock={() => handleLock(c.id)}
            onFork={() => {
              setForkSource(c);
              setForkOpen(true);
            }}
          />
        ))}
      </div>

      <ForkModal
        open={forkOpen}
        onClose={() => setForkOpen(false)}
        source={forkSource}
        onForked={() => {
          setForkOpen(false);
          refresh();
        }}
      />
    </div>
  );
}

function ConfigCard({
  config,
  isActive,
  onLock,
  onFork,
}: {
  config: ConfigVersion;
  isActive: boolean;
  onLock: () => void;
  onFork: () => void;
}) {
  return (
    <Card className={`p-space-lg ${isActive ? "ring-1 ring-primary" : ""}`}>
      <div className="flex items-center justify-between flex-wrap gap-space-sm mb-space-md">
        <div className="flex items-center gap-space-sm">
          <span className="px-space-sm py-0.5 rounded-full bg-surface-container-high font-mono text-label-md">{config.version_label}</span>
          <h3 className="font-headline-md text-headline-md">{config.title || "Untitled configuration"}</h3>
          <span className={`px-space-sm py-0.5 rounded-full text-label-sm capitalize ${config.status === "locked" ? "bg-tertiary-fixed text-on-tertiary-fixed-variant" : "bg-surface-container-high text-on-surface-variant"}`}>
            {config.status === "locked" ? "🔒 Locked & Certified" : config.status}
          </span>
        </div>
        <div className="flex items-center gap-space-sm">
          {config.status !== "locked" && (
            <Button variant="secondary" icon="lock" onClick={onLock}>
              Lock
            </Button>
          )}
          <Button variant="secondary" icon="call_split" onClick={onFork}>
            Fork
          </Button>
        </div>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-space-md text-label-md">
        <Kv label="Patch Dimension" value={`${config.patch_width}x${config.patch_height}`} />
        <Kv label="Stride" value={`${config.stride_x}px`} />
        <Kv label="Magnification" value={`${config.target_magnification}x`} />
        <Kv label="Tissue Threshold" value={`>=${Math.round(config.min_tissue_fraction * 100)}%`} />
      </div>
      <div className="mt-space-md pt-space-sm border-t border-outline-variant flex items-center gap-space-sm text-label-sm text-on-surface-variant font-mono">
        <MaterialIcon name="fingerprint" className="!text-[14px]" />
        {config.config_hash?.slice(0, 16)}...
        <span className="ml-space-md">Coordinate Frame: Level-0 Absolute (px)</span>
      </div>
    </Card>
  );
}

function Kv({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-on-surface-variant text-label-sm">{label}</div>
      <div className="font-mono">{value}</div>
    </div>
  );
}

function ForkModal({
  open,
  onClose,
  source,
  onForked,
}: {
  open: boolean;
  onClose: () => void;
  source: ConfigVersion | null;
  onForked: () => void;
}) {
  const pushToast = useUiStore((s) => s.pushToast);
  const [label, setLabel] = useState("v2.0");
  const [patchWidth, setPatchWidth] = useState(source?.patch_width ?? 512);
  const [strideX, setStrideX] = useState(source?.stride_x ?? 512);
  const [targetMag, setTargetMag] = useState(source?.target_magnification ?? 20);
  const [minTissue, setMinTissue] = useState(Math.round((source?.min_tissue_fraction ?? 0.6) * 100));
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (source) {
      setPatchWidth(source.patch_width);
      setStrideX(source.stride_x);
      setTargetMag(source.target_magnification ?? 20);
      setMinTissue(Math.round(source.min_tissue_fraction * 100));
    }
  }, [source]);

  if (!source) return null;

  async function handleSubmit() {
    setBusy(true);
    try {
      await forkConfig(source!.id, {
        new_version_label: label,
        overrides: {
          patch_width: patchWidth,
          patch_height: patchWidth,
          stride_x: strideX,
          stride_y: strideX,
          target_magnification: targetMag,
          min_tissue_fraction: minTissue / 100,
        },
      });
      pushToast("New config version created", "success");
      onForked();
    } catch (e) {
      pushToast(e instanceof Error ? e.message : "Fork failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose}>
      <div className="p-space-lg flex flex-col gap-space-md">
        <h2 className="font-headline-md text-headline-md">Fork Configuration from {source.version_label}</h2>
        <p className="text-body-md text-on-surface-variant">
          Existing patches and annotations stay linked to {source.version_label}. This creates an independent draft
          version -- nothing already generated is modified.
        </p>
        <label className="flex flex-col gap-1">
          <span className="text-label-md text-on-surface-variant">New Version Label</span>
          <input className="input" value={label} onChange={(e) => setLabel(e.target.value)} />
        </label>
        <div className="grid grid-cols-2 gap-space-md">
          <label className="flex flex-col gap-1">
            <span className="text-label-md text-on-surface-variant">Patch Size</span>
            <input type="number" className="input" value={patchWidth} onChange={(e) => setPatchWidth(Number(e.target.value))} />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-label-md text-on-surface-variant">Stride</span>
            <input type="number" className="input" value={strideX} onChange={(e) => setStrideX(Number(e.target.value))} />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-label-md text-on-surface-variant">Target Magnification</span>
            <input type="number" className="input" value={targetMag} onChange={(e) => setTargetMag(Number(e.target.value))} />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-label-md text-on-surface-variant">Min Tissue %</span>
            <input type="number" className="input" value={minTissue} onChange={(e) => setMinTissue(Number(e.target.value))} />
          </label>
        </div>
        <div className="flex justify-end gap-space-sm">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={handleSubmit} disabled={busy}>
            {busy ? "Creating..." : "Create New Version"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
