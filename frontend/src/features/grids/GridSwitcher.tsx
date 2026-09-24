import { useEffect, useState } from "react";
import { MaterialIcon } from "../../components/MaterialIcon";
import { Button, Modal } from "../../components/primitives";
import { generatePatches, listGrids, removeSlideGrid, setActiveGrid } from "../../services/api";
import { useUiStore } from "../../stores/uiStore";
import type { GridSpec, PatchGrid } from "../../types/api";
import { gridLabel, gridProblem } from "../../utils/gridKey";
import { GridForm } from "./GridForm";

const NEW = "__new__";
const REMOVE = "__remove__";

interface Props {
  slideId: number;
  configVersionId: number | null;
  /** Changes when the slide's grids may have changed elsewhere (e.g. Generate Coords). */
  refreshKey?: unknown;
  /** After switching to another grid or making a new one: reload the patches. */
  onChanged: (gridKey: string) => void;
  disabled?: boolean;
}

/**
 * Which patch size the slide is annotated in. Every grid shows every annotation (they are stored in
 * slide pixels too), so switching loses nothing; "New patch size" cuts another grid from the tissue mask.
 */
export function GridSwitcher({ slideId, configVersionId, refreshKey, onChanged, disabled }: Props) {
  const pushToast = useUiStore((s) => s.pushToast);
  const [grids, setGrids] = useState<PatchGrid[]>([]);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<GridSpec | null>(null); // the "new patch size" dialog, when open
  const [confirmRemove, setConfirmRemove] = useState<PatchGrid | null>(null);

  useEffect(() => {
    let cancelled = false;
    listGrids(slideId)
      .then((g) => !cancelled && setGrids(g))
      .catch(() => !cancelled && setGrids([]));
    return () => {
      cancelled = true;
    };
  }, [slideId, refreshKey, configVersionId]);

  const active = grids.find((g) => g.active) ?? null;

  async function run(work: () => Promise<string>) {
    setBusy(true);
    try {
      const key = await work();
      setGrids(await listGrids(slideId));
      onChanged(key);
    } catch (e) {
      pushToast(e instanceof Error ? e.message : "Could not change the patch size", "error");
    } finally {
      setBusy(false);
    }
  }

  const generate = (spec: GridSpec) =>
    run(async () => {
      if (configVersionId === null) throw new Error("This slide has no configuration");
      const res = await generatePatches(slideId, configVersionId, spec);
      pushToast(`${res.kept.toLocaleString()} patches of ${res.grid_label}`, "success");
      return res.grid_key;
    });

  function pick(key: string) {
    if (key === NEW) {
      setDraft({ ...(active ?? grids.find((g) => g.is_default))!.spec });
      return;
    }
    if (key === REMOVE) {
      if (active) setConfirmRemove(active);
      return;
    }
    const grid = grids.find((g) => g.key === key);
    if (!grid || grid.active) return;
    if (grid.patch_count === 0) generate(grid.spec); // the configuration's grid, not cut for this slide yet
    else run(async () => (await setActiveGrid(slideId, key)).active_grid_key ?? key);
  }

  if (grids.length === 0) return null;
  const problem = draft ? gridProblem(draft) : null;

  return (
    <>
      <label className="flex items-center gap-1.5" title="Patch size to annotate in. Every annotation shows in every patch size.">
        <MaterialIcon name="grid_view" className="!text-[16px] text-slate-400" />
        <select
          value={active?.key ?? ""}
          onChange={(e) => pick(e.target.value)}
          disabled={busy || disabled}
          className="bg-[#1e293b] border border-slate-700 rounded px-space-sm py-1 text-label-md text-white disabled:opacity-60 max-w-[22rem]"
          aria-label="Patch size"
        >
          {!active && <option value="">Patch size...</option>}
          {grids.map((g) => (
            <option key={g.key} value={g.key}>
              {g.label}
              {g.patch_count ? ` - ${g.patch_count.toLocaleString()} patches` : " - not made yet"}
              {g.is_default ? " (project)" : ""}
            </option>
          ))}
          <option value={NEW}>+ New patch size...</option>
          {active && active.patch_count > 0 && <option value={REMOVE}>Remove this patch size...</option>}
        </select>
        {busy && <span className="text-label-sm text-slate-400">Working...</span>}
      </label>

      <Modal open={!!confirmRemove} onClose={() => setConfirmRemove(null)} widthClass="max-w-lg">
        {confirmRemove && (
          <div className="p-space-lg flex flex-col gap-space-md text-on-surface">
            <h2 className="font-headline-md text-headline-md">Remove this patch size from the slide?</h2>
            <p className="text-body-md">
              <strong>{confirmRemove.label}</strong> - {confirmRemove.patch_count.toLocaleString()} patches
              {confirmRemove.annotated_patch_count ? `, ${confirmRemove.annotated_patch_count.toLocaleString()} of them annotated` : ""}.
            </p>
            <p className="text-body-md text-on-surface-variant">
              The patches go (with their status, flags and notes). Annotations drawn in them are <strong>kept</strong>, as
              whole-slide annotations at the same place, so they still show in every other patch size. The slide switches to
              another patch size it has.
            </p>
            <div className="flex justify-end gap-space-sm">
              <Button variant="ghost" onClick={() => setConfirmRemove(null)}>
                Cancel
              </Button>
              <Button
                variant="danger"
                icon="delete"
                disabled={busy}
                onClick={() => {
                  const grid = confirmRemove;
                  setConfirmRemove(null);
                  run(async () => {
                    const res = await removeSlideGrid(slideId, grid.key);
                    pushToast(
                      `Removed ${res.patches.toLocaleString()} patches` + (res.annotations_kept ? `; ${res.annotations_kept} annotations kept on the whole slide` : ""),
                      "success",
                    );
                    return (await listGrids(slideId)).find((g) => g.active)?.key ?? "";
                  });
                }}
              >
                Remove patch size
              </Button>
            </div>
          </div>
        )}
      </Modal>

      <Modal open={!!draft} onClose={() => setDraft(null)} widthClass="max-w-2xl">
        {draft && (
          <div className="p-space-lg flex flex-col gap-space-md text-on-surface">
            <div className="flex items-center justify-between">
              <h2 className="font-headline-md text-headline-md">New patch size</h2>
              <button onClick={() => setDraft(null)} aria-label="Close">
                <MaterialIcon name="close" />
              </button>
            </div>
            <p className="text-body-md text-on-surface-variant">
              Cuts this slide into another grid of patches from its tissue mask, and switches to it. Nothing already drawn
              changes: every annotation shows in every patch size, and you can switch back at any time.
            </p>
            <GridForm value={draft} onChange={setDraft} />
            <div className="text-body-sm text-on-surface-variant">{problem ?? gridLabel(draft)}</div>
            <div className="flex justify-end gap-space-sm">
              <Button variant="ghost" onClick={() => setDraft(null)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                icon="grid_view"
                disabled={!!problem || busy}
                onClick={() => {
                  const spec = draft;
                  setDraft(null);
                  generate(spec);
                }}
              >
                Make patches
              </Button>
            </div>
          </div>
        )}
      </Modal>
    </>
  );
}
