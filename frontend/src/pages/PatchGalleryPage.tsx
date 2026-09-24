import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { MaterialIcon } from "../components/MaterialIcon";
import { Pagination } from "../components/Pagination";
import { Button, Card, StatusPill } from "../components/primitives";
import { usePageParams } from "../utils/pagination";
import {
  getConfig,
  getPatchLabelCounts,
  getSlide,
  labelPatches,
  listPatches,
  NO_LABEL,
  patchPreviewUrl,
  type PatchSort,
} from "../services/api";
import { OTHER_PATCH_LABELS } from "../features/patches/labels";
import { useUiStore } from "../stores/uiStore";
import type { ConfigVersion, Patch, PatchStatus, Slide } from "../types/api";

const DENSITY_COLS: Record<string, string> = {
  compact: "grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8",
  standard: "grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6",
  large: "grid-cols-1 sm:grid-cols-2 md:grid-cols-3",
};

type FilterKey = "all" | PatchStatus | "flagged";

import { useImageProjectRedirect } from "../features/images/useImageProjectRedirect";

const SORTS: { id: PatchSort; label: string }[] = [
  { id: "index", label: "Patch order" },
  { id: "tissue_desc", label: "Most tissue first" },
  { id: "tissue_asc", label: "Least tissue first" },
];

export function PatchGalleryPage() {
  const { projectId, slideId } = useParams();
  const pid = Number(projectId);
  const sid = Number(slideId);
  useImageProjectRedirect(pid);
  const navigate = useNavigate();

  const [slide, setSlide] = useState<Slide | null>(null);
  const [config, setConfig] = useState<ConfigVersion | null>(null);
  const [patches, setPatches] = useState<Patch[]>([]);
  const [total, setTotal] = useState(0);
  const { page, pageSize, setPage, setPageSize } = usePageParams(24);
  const [filter, setFilter] = useState<FilterKey>("all");
  const [density, setDensity] = useState<"compact" | "standard" | "large">("standard");
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [label, setLabel] = useState(""); // "" any label, NO_LABEL none, else that label
  const [sort, setSort] = useState<PatchSort>("index");
  const [labelCounts, setLabelCounts] = useState<{ label: string | null; count: number }[]>([]);
  // Selecting patches to label them together; kept across pages until labelled or cleared.
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [batchLabel, setBatchLabel] = useState("");
  const [applying, setApplying] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const pushToast = useUiStore((s) => s.pushToast);

  useEffect(() => {
    getSlide(sid).then((s) => {
      setSlide(s);
      if (s.active_config_version_id) getConfig(s.active_config_version_id).then(setConfig);
    });
  }, [sid]);

  useEffect(() => {
    const params: Parameters<typeof listPatches>[1] = { limit: pageSize, offset: page * pageSize };
    if (filter === "flagged") params.flagged = true;
    else if (filter !== "all") params.status = filter;
    if (label) params.label = label;
    if (sort !== "index") params.sort = sort;
    listPatches(sid, params).then((res) => {
      setPatches(res.items);
      setTotal(res.total);
    });
  }, [sid, filter, label, sort, page, pageSize, refresh]);

  useEffect(() => {
    getPatchLabelCounts(sid).then(setLabelCounts).catch(() => setLabelCounts([]));
  }, [sid, refresh]);

  // A page past the end (fewer patches than when the link was made): show the last one instead.
  useEffect(() => {
    const last = Math.max(0, Math.ceil(total / pageSize) - 1);
    if (total > 0 && page > last) setPage(last);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [total, pageSize, page]);

  // The page controls are at the bottom: start each new page at the top.
  useEffect(() => {
    window.scrollTo({ top: 0 }); // in braces: newer browsers return a Promise here, and an effect may only return a cleanup
  }, [page, pageSize]);

  useEffect(() => {
    (async () => {
      const statuses: FilterKey[] = ["unannotated", "annotated", "reviewed", "skipped"];
      const entries = await Promise.all(
        statuses.map(async (s) => [s, (await listPatches(sid, { status: s, limit: 1 })).total] as const),
      );
      const flaggedTotal = (await listPatches(sid, { flagged: true, limit: 1 })).total;
      setCounts(Object.fromEntries([...entries, ["flagged", flaggedTotal]]));
    })();
  }, [sid, patches.length, refresh]);

  const filterPills: { key: FilterKey; label: string }[] = useMemo(
    () => [
      { key: "all", label: `All (${total})` },
      { key: "unannotated", label: `Unannotated (${counts.unannotated ?? 0})` },
      { key: "annotated", label: `Annotated (${counts.annotated ?? 0})` },
      { key: "reviewed", label: `Reviewed (${counts.reviewed ?? 0})` },
      { key: "skipped", label: `Skipped (${counts.skipped ?? 0})` },
      { key: "flagged", label: `Flagged (${counts.flagged ?? 0})` },
    ],
    [total, counts],
  );

  if (!slide) return <div className="p-space-xl text-center text-on-surface-variant">Loading...</div>;

  const classNames = config?.annotation_classes.map((c) => c.name) ?? [];
  const labelChoices = [...classNames, ...OTHER_PATCH_LABELS];
  const pageIds = patches.map((p) => p.id);
  const allPageSelected = pageIds.length > 0 && pageIds.every((id) => selected.has(id));

  function toggle(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function selectPage(on: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const id of pageIds) {
        if (on) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  }

  async function applyLabel() {
    if (!selected.size || !batchLabel) return;
    const value = batchLabel === NO_LABEL ? null : batchLabel;
    setApplying(true);
    try {
      const res = await labelPatches(sid, [...selected], value);
      pushToast(value ? `Labelled ${res.updated} patches "${value}"` : `Removed the label from ${res.updated} patches`, "success");
      setSelected(new Set());
      setRefresh((n) => n + 1);
    } catch (e) {
      pushToast(e instanceof Error ? e.message : "Could not label the patches", "error");
    } finally {
      setApplying(false);
    }
  }

  return (
    <div className="max-w-[1720px] mx-auto px-gutter sm:px-margin py-space-lg flex flex-col gap-space-md">
      <Card className="p-space-sm sticky top-14 z-20 flex flex-col gap-space-sm">
        <div className="flex items-center justify-between flex-wrap gap-space-sm">
          <div className="flex items-center gap-space-sm">
            <span className="font-headline-md text-headline-md">{slide.filename}</span>
            <span className="px-space-sm py-0.5 rounded-full bg-surface-container-high text-label-sm font-mono">
              {total.toLocaleString()} Tiles
            </span>
          </div>
          <div className="flex bg-surface-container-low rounded p-0.5">
            {(["compact", "standard", "large"] as const).map((d) => (
              <button
                key={d}
                onClick={() => setDensity(d)}
                className={`px-space-sm py-1 rounded text-label-sm capitalize ${density === d ? "bg-surface-container-lowest shadow-sm" : ""}`}
              >
                {d}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-space-sm flex-wrap">
          {filterPills.map((f) => (
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
        <div className="flex items-center gap-space-sm flex-wrap">
          <select
            className="input !w-auto !py-1"
            value={label}
            aria-label="Filter by patch label"
            onChange={(e) => {
              setLabel(e.target.value);
              setPage(0);
            }}
          >
            <option value="">Any label</option>
            {labelCounts.map((c) => (
              <option key={c.label ?? NO_LABEL} value={c.label ?? NO_LABEL}>
                {c.label ?? "No label"} ({c.count})
              </option>
            ))}
          </select>
          <select
            className="input !w-auto !py-1"
            value={sort}
            aria-label="Sort patches"
            onChange={(e) => {
              setSort(e.target.value as PatchSort);
              setPage(0);
            }}
          >
            {SORTS.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
          <Button
            variant={selecting ? "primary" : "secondary"}
            icon="checklist"
            onClick={() => {
              setSelecting((v) => !v);
              setSelected(new Set());
            }}
          >
            {selecting ? "Done selecting" : "Select to label"}
          </Button>
        </div>
        {selecting && (
          <div className="flex items-center gap-space-sm flex-wrap rounded bg-primary-fixed/40 px-space-sm py-1.5" data-testid="batch-label-bar">
            <span className="text-label-md">{selected.size} selected</span>
            <Button variant="ghost" onClick={() => selectPage(!allPageSelected)} disabled={!pageIds.length}>
              {allPageSelected ? "Unselect this page" : "Select this page"}
            </Button>
            {selected.size > 0 && (
              <Button variant="ghost" onClick={() => setSelected(new Set())}>
                Clear
              </Button>
            )}
            <span className="flex-1" />
            <span className="text-label-md text-on-surface-variant">Label as</span>
            <select className="input !w-auto !py-1" value={batchLabel} onChange={(e) => setBatchLabel(e.target.value)} aria-label="Label for the selected patches">
              <option value="">Choose...</option>
              {labelChoices.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
              <option value={NO_LABEL}>(remove the label)</option>
            </select>
            <Button variant="primary" icon="label" onClick={applyLabel} disabled={!selected.size || !batchLabel || applying}>
              {applying ? "Labelling..." : `Apply to ${selected.size}`}
            </Button>
          </div>
        )}
        {selecting && (
          <div className="text-label-sm text-on-surface-variant">
            A class fills each patch with that class, just like choosing it in the workspace; Mixed and Artifact are labels only.
          </div>
        )}
        {config && (
          <div className="text-label-sm text-on-surface-variant font-mono">
            {config.patch_width}x{config.patch_height}px @ {config.target_magnification}x &middot; Tissue Threshold &gt;={" "}
            {Math.round(config.min_tissue_fraction * 100)}%
          </div>
        )}
      </Card>

      {patches.length === 0 ? (
        <Card className="p-space-xl text-center text-on-surface-variant">
          No patches match this filter. Generate patches from the Slide Processing screen first.
        </Card>
      ) : (
        <div className={`grid ${DENSITY_COLS[density]} gap-space-md`}>
          {patches.map((p) => (
            <PatchCard
              key={p.id}
              patch={p}
              slide={slide}
              selecting={selecting}
              selected={selected.has(p.id)}
              onToggle={() => toggle(p.id)}
              onOpen={() => navigate(`/projects/${pid}/slides/${sid}/workspace?patch=${p.id}`)}
            />
          ))}
        </div>
      )}

      <Pagination page={page} pageSize={pageSize} total={total} onPage={setPage} onPageSize={setPageSize} noun="patches" />
    </div>
  );
}

function PatchCard({
  patch,
  slide,
  selecting,
  selected,
  onToggle,
  onOpen,
}: {
  patch: Patch;
  slide: Slide;
  selecting: boolean;
  selected: boolean;
  onToggle: () => void;
  onOpen: () => void;
}) {
  return (
    <div
      className={`group relative flex flex-col bg-surface-container-lowest rounded-xl overflow-hidden shadow-sm hover:shadow-md transition-all ${
        selected ? "ring-2 ring-primary" : ""
      }`}
    >
      <div className="flex items-center justify-between px-space-sm py-1.5 bg-surface-container-high/60">
        <span className="font-mono text-label-md">#{patch.patch_index}</span>
        <span className="font-mono text-label-sm text-secondary">
          X:{patch.x.toLocaleString()} Y:{patch.y.toLocaleString()}
        </span>
      </div>
      <div className="relative aspect-square w-full bg-surface-dim overflow-hidden cursor-pointer" onClick={selecting ? onToggle : onOpen}>
        <img
          src={patchPreviewUrl(slide, patch)}
          alt={`Patch ${patch.patch_index}`}
          className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
          loading="lazy"
        />
        <div className="absolute top-1.5 right-1.5">
          <StatusPill status={patch.status} />
        </div>
        {selecting && (
          <div className="absolute top-1.5 left-1.5 z-10 w-6 h-6 rounded bg-surface-container-lowest/90 flex items-center justify-center">
            <MaterialIcon name={selected ? "check_box" : "check_box_outline_blank"} className="text-primary !text-[20px]" />
          </div>
        )}
        {patch.patch_label && (
          <div className="absolute bottom-1.5 left-1.5 px-space-sm py-0.5 rounded-full bg-surface-container-lowest/90 backdrop-blur text-label-sm">
            {patch.patch_label}
          </div>
        )}
        {!selecting && (
          <div className="absolute inset-0 bg-on-background/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
            <span className="px-space-md py-1.5 rounded bg-primary text-on-primary text-label-md flex items-center gap-1">
              <MaterialIcon name="edit" className="!text-[16px]" />
              Annotate
            </span>
          </div>
        )}
      </div>
      <div className="p-space-sm flex items-center justify-between text-label-sm text-on-surface-variant">
        <span>Tissue: {Math.round(patch.tissue_fraction * 100)}%</span>
        {patch.flagged && <MaterialIcon name="flag" className="!text-[14px] text-error" />}
      </div>
    </div>
  );
}
