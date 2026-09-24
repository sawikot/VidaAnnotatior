import { useCallback, useEffect, useState } from "react";
import { MaterialIcon } from "../../components/MaterialIcon";
import { Button, Card, Modal } from "../../components/primitives";
import { Toggle } from "../../components/formControls";
import { addProjectGrid, listProjectGrids, removeProjectGrid, updateConfig } from "../../services/api";
import { useUiStore } from "../../stores/uiStore";
import type { ConfigVersion, GridSpec, ProjectGrid } from "../../types/api";
import { gridLabel, gridProblem } from "../../utils/gridKey";
import { GridForm } from "./GridForm";

interface Props {
  projectId: number;
  config: ConfigVersion;
  /** After the project's grid changed (Use as default): reload the settings. */
  onConfigChanged: () => void;
}

/**
 * Every patch size used in the project -- how many slides have it, how many patches and annotations --
 * with making one the project's default, or removing one from every slide.
 */
export function PatchSizesPanel({ projectId, config, onConfigChanged }: Props) {
  const pushToast = useUiStore((s) => s.pushToast);
  const [grids, setGrids] = useState<ProjectGrid[] | null>(null);
  const [removing, setRemoving] = useState<ProjectGrid | null>(null);
  const [adding, setAdding] = useState<GridSpec | null>(null); // the "add patch size" dialog, when open
  const [makeDefaultToo, setMakeDefaultToo] = useState(false);
  const [skipped, setSkipped] = useState<{ slide: string; reason: string }[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    listProjectGrids(projectId)
      .then(setGrids)
      .catch(() => setGrids([]));
  }, [projectId]);

  useEffect(load, [load, config.updated_at]);

  async function makeDefault(g: ProjectGrid) {
    setBusy(true);
    try {
      const { patch_width, patch_height, stride_x, stride_y, target_magnification, min_tissue_fraction, include_edge_patches, allow_partial_patches } = g.spec;
      await updateConfig(config.id, { patch_width, patch_height, stride_x, stride_y, target_magnification, min_tissue_fraction, include_edge_patches, allow_partial_patches });
      pushToast(`Generate Coords now uses ${g.label}`, "success");
      onConfigChanged();
    } catch (e) {
      pushToast(e instanceof Error ? e.message : "Could not change the default", "error");
    } finally {
      setBusy(false);
    }
  }

  async function add(spec: GridSpec) {
    setBusy(true);
    try {
      const res = await addProjectGrid(projectId, spec, makeDefaultToo);
      pushToast(
        res.slides
          ? `${res.patches.toLocaleString()} patches of ${res.grid_label} on ${res.slides} slide${res.slides === 1 ? "" : "s"}`
          : "No slide could be cut into that patch size",
        res.slides ? "success" : "error",
      );
      setSkipped(res.skipped);
      setAdding(null);
      if (makeDefaultToo) onConfigChanged();
      else load();
    } catch (e) {
      pushToast(e instanceof Error ? e.message : "Could not add the patch size", "error");
    } finally {
      setBusy(false);
    }
  }

  async function remove(g: ProjectGrid) {
    setBusy(true);
    try {
      const res = await removeProjectGrid(projectId, g.key);
      pushToast(
        `Removed ${res.patches.toLocaleString()} patches from ${res.slides} slide${res.slides === 1 ? "" : "s"}` +
          (res.annotations_kept ? `; ${res.annotations_kept} annotations kept on the whole slide` : ""),
        "success",
      );
      setRemoving(null);
      load();
    } catch (e) {
      pushToast(e instanceof Error ? e.message : "Could not remove the patch size", "error");
    } finally {
      setBusy(false);
    }
  }

  if (!grids) return null;

  return (
    <Card className="p-space-md flex flex-col gap-space-sm">
      <div className="flex items-start justify-between gap-space-md flex-wrap">
        <div className="flex-1 min-w-[16rem]">
          <h2 className="font-headline-sm text-headline-sm">Patch sizes</h2>
          <p className="text-body-sm text-on-surface-variant">
            The patch sizes slides are cut into. The default is what <em>Generate Coords</em> cuts; a slide can hold several
            and switch between them in the workspace. Every annotation shows in every patch size.
          </p>
        </div>
        <Button
          variant="primary"
          icon="add"
          disabled={busy}
          onClick={() => {
            setMakeDefaultToo(false);
            setAdding({ ...(grids.find((g) => g.is_default) ?? grids[0]).spec });
          }}
        >
          Add patch size
        </Button>
      </div>
      {skipped.length > 0 && (
        <div className="rounded bg-surface-container-low px-space-md py-space-sm text-body-sm flex flex-col gap-0.5">
          <div className="flex items-center justify-between">
            <span className="text-label-md">Not cut into the new patch size:</span>
            <button className="text-on-surface-variant hover:underline" onClick={() => setSkipped([])}>
              Dismiss
            </button>
          </div>
          {skipped.map((s) => (
            <span key={s.slide}>
              <span className="font-mono">{s.slide}</span> -- {s.reason}
            </span>
          ))}
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-body-sm">
          <thead className="text-label-md text-on-surface-variant text-left">
            <tr>
              <th className="py-1 pr-space-md">Patch size</th>
              <th className="py-1 pr-space-md text-right">Slides</th>
              <th className="py-1 pr-space-md text-right">Patches</th>
              <th className="py-1 pr-space-md text-right">Annotated</th>
              <th className="py-1" />
            </tr>
          </thead>
          <tbody>
            {grids.map((g) => (
              <tr key={g.key} className="border-t border-outline-variant">
                <td className="py-1.5 pr-space-md">
                  {g.label}
                  {g.is_default && <span className="ml-1.5 px-1.5 rounded-full bg-primary-fixed text-label-sm">default</span>}
                </td>
                <td className="py-1.5 pr-space-md text-right font-mono">{g.slide_count}</td>
                <td className="py-1.5 pr-space-md text-right font-mono">{g.patch_count ? g.patch_count.toLocaleString() : "not made yet"}</td>
                <td className="py-1.5 pr-space-md text-right font-mono">{g.annotated_patch_count.toLocaleString()}</td>
                <td className="py-1.5 text-right whitespace-nowrap">
                  {!g.is_default && (
                    <button className="text-primary hover:underline mr-space-sm disabled:opacity-40" disabled={busy} onClick={() => makeDefault(g)}>
                      Use as default
                    </button>
                  )}
                  {g.patch_count > 0 && (
                    <button className="text-error hover:underline disabled:opacity-40" disabled={busy} onClick={() => setRemoving(g)}>
                      Remove
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Modal open={!!adding} onClose={() => !busy && setAdding(null)} widthClass="max-w-2xl">
        {adding && (
          <div className="p-space-lg flex flex-col gap-space-md">
            <div className="flex items-center justify-between">
              <h2 className="font-headline-md text-headline-md">Add patch size</h2>
              <button onClick={() => setAdding(null)} aria-label="Close" disabled={busy}>
                <MaterialIcon name="close" />
              </button>
            </div>
            <p className="text-body-md text-on-surface-variant">
              Cuts every slide whose tissue has been found into this patch size, from its tissue mask. Slides keep showing
              the size they are on; the new one is in their patch-size menu. Nothing already drawn changes.
            </p>
            <GridForm value={adding} onChange={setAdding} />
            <Toggle label="Make it the default (what Generate Coords cuts)" checked={makeDefaultToo} onChange={setMakeDefaultToo} />
            <div className="text-body-sm text-on-surface-variant">{gridProblem(adding) ?? gridLabel(adding)}</div>
            <div className="flex justify-end gap-space-sm">
              <Button variant="ghost" onClick={() => setAdding(null)} disabled={busy}>
                Cancel
              </Button>
              <Button variant="primary" icon="grid_view" onClick={() => add(adding)} disabled={busy || !!gridProblem(adding)}>
                {busy ? "Cutting patches..." : "Add patch size"}
              </Button>
            </div>
          </div>
        )}
      </Modal>

      <Modal open={!!removing} onClose={() => !busy && setRemoving(null)} widthClass="max-w-lg">
        {removing && (
          <div className="p-space-lg flex flex-col gap-space-md">
            <h2 className="font-headline-md text-headline-md flex items-center gap-space-sm">
              <MaterialIcon name="delete" className="text-error" />
              Remove this patch size?
            </h2>
            <p className="text-body-md">
              <strong>{removing.label}</strong>
            </p>
            <ul className="text-body-md list-disc pl-space-lg flex flex-col gap-1">
              <li>
                Deletes its <strong>{removing.patch_count.toLocaleString()}</strong> patches from{" "}
                <strong>
                  {removing.slide_count} slide{removing.slide_count === 1 ? "" : "s"}
                </strong>
                , with their status, flags and notes.
              </li>
              {removing.annotation_count > 0 ? (
                <li>
                  {removing.annotation_count === 1 ? "Its annotation is" : <>Its <strong>{removing.annotation_count.toLocaleString()}</strong> annotations are</>}{" "}
                  <strong>kept</strong>, as
                  whole-slide annotations at the same place: they still show in every other patch size and in exports.
                </li>
              ) : (
                <li>No annotations were drawn in it.</li>
              )}
              <li>Slides using it switch to another patch size they have. You can make it again at any time.</li>
            </ul>
            <div className="flex justify-end gap-space-sm">
              <Button variant="ghost" onClick={() => setRemoving(null)} disabled={busy}>
                Cancel
              </Button>
              <Button variant="danger" icon="delete" onClick={() => remove(removing)} disabled={busy}>
                {busy ? "Removing..." : "Remove patch size"}
              </Button>
            </div>
          </div>
        )}
      </Modal>
    </Card>
  );
}
