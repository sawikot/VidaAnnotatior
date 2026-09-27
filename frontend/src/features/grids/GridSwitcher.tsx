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
 * The slide's patch size, among every size made in the project (saved until removed in Settings).
 * Picking another -- or "New patch size" -- cuts the slide into it, *replacing* its current patches
 * (annotations drawn in them are kept on the whole slide), and makes it the project's default.
 */
export function GridSwitcher({ slideId, configVersionId, refreshKey, onChanged, disabled }: Props) {
  const pushToast = useUiStore((s) => s.pushToast);
  const [grids, setGrids] = useState<PatchGrid[]>([]);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<GridSpec | null>(null); // the "new patch size" dialog, when open
  const [confirmRemove, setConfirmRemove] = useState<PatchGrid | null>(null);
  const [confirmSwitch, setConfirmSwitch] = useState<PatchGrid | null>(null); // keep only this one of several

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
      pushToast(
        `${res.kept.toLocaleString()} patches of ${res.grid_label}` +
          (res.replaced_grids ? ` (replaced ${res.replaced_patches.toLocaleString()} earlier patches)` : ""),
        "success",
      );
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
    if (grid.patch_count === 0) setDraft({ ...grid.spec }); // a saved size: confirm there, it replaces the current patches
    else setConfirmSwitch(grid); // the slide still has several sizes: keeping one removes the others
  }

  if (grids.length === 0) return null;
  const problem = draft ? gridProblem(draft) : null;

  return (
    <>
      <label className="flex items-center gap-1.5" title="This slide's patch size. A new size replaces the current patches; annotations are kept on the whole slide.">
        <MaterialIcon name="grid_view" className="!text-[16px] text-slate-400" />
        <select
          value={active?.key ?? ""}
          onChange={(e) => pick(e.target.value)}
          disabled={busy || disabled}
          className="bg-[#1e293b] border border-slate-700 rounded px-space-sm py-1 text-label-md text-white disabled:opacity-60 max-w-[22rem]"
          aria-label="Patch size"
        >
          {!active && <option value="">Patch size...</option>}
          {/* Every size made in the project stays listed; the one with patches here is this slide's. */}
          {grids.map((g) => (
            <option key={g.key} value={g.key}>
              {g.label}
              {g.patch_count ? ` - ${g.patch_count.toLocaleString()} patches` : " - saved size"}
              {g.is_default ? " (project default)" : ""}
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

      <Modal open={!!confirmSwitch} onClose={() => setConfirmSwitch(null)} widthClass="max-w-lg">
        {confirmSwitch &&
          (() => {
            const others = grids.filter((g) => g.key !== confirmSwitch.key && g.patch_count > 0);
            const patchesGone = others.reduce((n, g) => n + g.patch_count, 0);
            const annotatedGone = others.reduce((n, g) => n + g.annotated_patch_count, 0);
            return (
              <div className="p-space-lg flex flex-col gap-space-md text-on-surface">
                <h2 className="font-headline-md text-headline-md">Use only this patch size?</h2>
                <p className="text-body-md">
                  <strong>{confirmSwitch.label}</strong> becomes this slide's patch size. A slide has one patch size, so the
                  other {others.length === 1 ? "one is" : `${others.length} are`} removed:
                </p>
                <ul className="text-body-md list-disc pl-space-lg flex flex-col gap-1">
                  {others.map((g) => (
                    <li key={g.key}>
                      {g.label} -- {g.patch_count.toLocaleString()} patches
                    </li>
                  ))}
                </ul>
                <p className="text-body-md text-on-surface-variant">
                  {annotatedGone > 0 ? (
                    <>
                      Annotations drawn in those patches are <strong>kept</strong>, on the whole slide at the same place. The{" "}
                      {patchesGone.toLocaleString()} patches' status, labels and notes are removed.
                    </>
                  ) : (
                    <>No annotations were drawn in them; their {patchesGone.toLocaleString()} patches are removed.</>
                  )}
                </p>
                <div className="flex justify-end gap-space-sm">
                  <Button variant="ghost" onClick={() => setConfirmSwitch(null)}>
                    Cancel
                  </Button>
                  <Button
                    variant="primary"
                    icon="grid_view"
                    disabled={busy}
                    onClick={() => {
                      const grid = confirmSwitch;
                      setConfirmSwitch(null);
                      run(async () => {
                        const key = (await setActiveGrid(slideId, grid.key)).active_grid_key ?? grid.key;
                        pushToast(`Now using ${grid.label}; removed ${patchesGone.toLocaleString()} patches of other sizes`, "success");
                        return key;
                      });
                    }}
                  >
                    Use only this size
                  </Button>
                </div>
              </div>
            );
          })()}
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
              Cuts this slide into patches of this size.{" "}
              {active && active.patch_count > 0 ? (
                <>
                  It <strong>replaces</strong> the current {active.patch_count.toLocaleString()} patches ({active.label}).
                  Annotations drawn in them are <strong>kept</strong>, on the whole slide at the same place; their patch
                  status, labels and notes are removed.
                </>
              ) : (
                "Nothing already drawn changes."
              )}
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
                {active && active.patch_count > 0 ? "Replace patches" : "Make patches"}
              </Button>
            </div>
          </div>
        )}
      </Modal>
    </>
  );
}
