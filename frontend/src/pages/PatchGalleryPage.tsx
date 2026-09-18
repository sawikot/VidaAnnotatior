import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { MaterialIcon } from "../components/MaterialIcon";
import { Button, Card, StatusPill } from "../components/primitives";
import { dynamicPatchUrl, getConfig, getSlide, listPatches } from "../services/api";
import type { ConfigVersion, Patch, PatchStatus, Slide } from "../types/api";

const PAGE_SIZE = 24;

const DENSITY_COLS: Record<string, string> = {
  compact: "grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8",
  standard: "grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6",
  large: "grid-cols-1 sm:grid-cols-2 md:grid-cols-3",
};

type FilterKey = "all" | PatchStatus | "flagged";

export function PatchGalleryPage() {
  const { projectId, slideId } = useParams();
  const pid = Number(projectId);
  const sid = Number(slideId);
  const navigate = useNavigate();

  const [slide, setSlide] = useState<Slide | null>(null);
  const [config, setConfig] = useState<ConfigVersion | null>(null);
  const [patches, setPatches] = useState<Patch[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [filter, setFilter] = useState<FilterKey>("all");
  const [density, setDensity] = useState<"compact" | "standard" | "large">("standard");
  const [counts, setCounts] = useState<Record<string, number>>({});

  useEffect(() => {
    getSlide(sid).then((s) => {
      setSlide(s);
      if (s.active_config_version_id) getConfig(s.active_config_version_id).then(setConfig);
    });
  }, [sid]);

  useEffect(() => {
    const params: Parameters<typeof listPatches>[1] = { limit: PAGE_SIZE, offset: page * PAGE_SIZE };
    if (filter === "flagged") params.flagged = true;
    else if (filter !== "all") params.status = filter;
    listPatches(sid, params).then((res) => {
      setPatches(res.items);
      setTotal(res.total);
    });
  }, [sid, filter, page]);

  useEffect(() => {
    (async () => {
      const statuses: FilterKey[] = ["unannotated", "annotated", "reviewed", "skipped"];
      const entries = await Promise.all(
        statuses.map(async (s) => [s, (await listPatches(sid, { status: s, limit: 1 })).total] as const),
      );
      const flaggedTotal = (await listPatches(sid, { flagged: true, limit: 1 })).total;
      setCounts(Object.fromEntries([...entries, ["flagged", flaggedTotal]]));
    })();
  }, [sid, patches.length]);

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

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
            <PatchCard key={p.id} patch={p} slideId={sid} onOpen={() => navigate(`/projects/${pid}/slides/${sid}/workspace?patch=${p.id}`)} />
          ))}
        </div>
      )}

      <div className="flex items-center justify-between">
        <span className="text-label-md text-on-surface-variant">
          Page {page + 1} of {pageCount}
        </span>
        <div className="flex items-center gap-space-sm">
          <Button variant="ghost" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
            Previous
          </Button>
          <Button variant="ghost" disabled={page >= pageCount - 1} onClick={() => setPage((p) => p + 1)}>
            Next
          </Button>
        </div>
      </div>
    </div>
  );
}

function PatchCard({ patch, slideId, onOpen }: { patch: Patch; slideId: number; onOpen: () => void }) {
  return (
    <div className="group relative flex flex-col bg-surface-container-lowest rounded-xl overflow-hidden shadow-sm hover:shadow-md transition-all">
      <div className="flex items-center justify-between px-space-sm py-1.5 bg-surface-container-high/60">
        <span className="font-mono text-label-md">#{patch.patch_index}</span>
        <span className="font-mono text-label-sm text-secondary">
          X:{patch.x.toLocaleString()} Y:{patch.y.toLocaleString()}
        </span>
      </div>
      <div className="relative aspect-square w-full bg-surface-dim overflow-hidden cursor-pointer" onClick={onOpen}>
        <img
          src={dynamicPatchUrl(slideId, patch.x, patch.y, patch.width, patch.height, patch.level)}
          alt={`Patch ${patch.patch_index}`}
          className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
          loading="lazy"
        />
        <div className="absolute top-1.5 right-1.5">
          <StatusPill status={patch.status} />
        </div>
        {patch.patch_label && (
          <div className="absolute top-1.5 left-1.5 px-space-sm py-0.5 rounded-full bg-surface-container-lowest/90 backdrop-blur text-label-sm">
            {patch.patch_label}
          </div>
        )}
        <div className="absolute inset-0 bg-on-background/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
          <span className="px-space-md py-1.5 rounded bg-primary text-on-primary text-label-md flex items-center gap-1">
            <MaterialIcon name="edit" className="!text-[16px]" />
            Annotate
          </span>
        </div>
      </div>
      <div className="p-space-sm flex items-center justify-between text-label-sm text-on-surface-variant">
        <span>Tissue: {Math.round(patch.tissue_fraction * 100)}%</span>
        {patch.flagged && <MaterialIcon name="flag" className="!text-[14px] text-error" />}
      </div>
    </div>
  );
}
