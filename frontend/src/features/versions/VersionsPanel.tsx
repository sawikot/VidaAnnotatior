import { useEffect, useState } from "react";
import { Field } from "../../components/formControls";
import { MaterialIcon } from "../../components/MaterialIcon";
import { Button, Card, ConfirmDeleteModal, IconButton, Modal } from "../../components/primitives";
import { deleteProjectVersion, listProjectVersions, openProjectVersion, saveProjectVersion, updateProjectVersion } from "../../services/api";
import { useCan } from "../../stores/authStore";
import { useUiStore } from "../../stores/uiStore";
import type { ProjectType, ProjectVersion, ProjectVersions } from "../../types/api";

// The server's times are UTC without saying so.
const when = (iso: string) =>
  new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

const label = (v: ProjectVersion) => `v${v.number}`;

function contents(v: ProjectVersion, projectType: ProjectType): string {
  const s = v.summary;
  const n = (count: number | undefined, one: string, many: string) => `${(count ?? 0).toLocaleString()} ${count === 1 ? one : many}`;
  const parts = [projectType === "image" ? n(s.slides, "image", "images") : n(s.slides, "slide", "slides")];
  if (projectType !== "image") parts.push(n(s.patches, "patch", "patches"));
  parts.push(n(s.annotations, "annotation", "annotations"));
  if (s.suggestions) parts.push(n(s.suggestions, "suggestion", "suggestions"));
  return parts.join(" · ");
}

type Dialog =
  | { kind: "save"; thenOpen?: ProjectVersion }
  | { kind: "rename"; version: ProjectVersion }
  | { kind: "unsaved"; version: ProjectVersion }
  | { kind: "discard"; version: ProjectVersion }
  | { kind: "delete"; version: ProjectVersion };

/** The project's saved versions, on its dashboard: save the work as a new version, open another one
 * (the project then shows only what that version holds) and go on from there. `revision` changes
 * whenever the page reloads the project, so "unsaved changes" stays true to it; `onSwitched` is
 * called when the work itself was replaced and everything shown must be loaded again. */
export function VersionsPanel({
  projectId,
  projectType,
  revision,
  onSwitched,
}: {
  projectId: number;
  projectType: ProjectType;
  revision: number;
  onSwitched: () => void;
}) {
  const canManage = useCan().manage;
  const pushToast = useUiStore((s) => s.pushToast);
  const [data, setData] = useState<ProjectVersions | null>(null);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // what is happening, in words
  const [name, setName] = useState("");
  const [note, setNote] = useState("");

  useEffect(() => {
    let stale = false;
    listProjectVersions(projectId)
      .then((d) => !stale && setData(d))
      .catch(() => undefined); // the page already reports a project that will not load
    return () => {
      stale = true;
    };
  }, [projectId, revision]);

  if (!data) return null;

  const { versions } = data;
  const current = versions.find((v) => v.id === data.current_version_id) ?? null;
  const byId = new Map(versions.map((v) => [v.id, v]));
  const nextNumber = Math.max(0, ...versions.map((v) => v.number)) + 1;
  const unsaved = data.has_unsaved_changes;

  function openDialog(d: Dialog) {
    setName(d.kind === "rename" ? d.version.name : "");
    setNote(d.kind === "rename" ? (d.version.note ?? "") : "");
    setDialog(d);
  }

  async function run(doing: string, action: () => Promise<void>) {
    setBusy(doing);
    try {
      await action();
      setDialog(null);
    } catch (e) {
      pushToast(e instanceof Error ? e.message : "That did not work", "error");
      listProjectVersions(projectId).then(setData).catch(() => undefined);
    } finally {
      setBusy(null);
    }
  }

  const open = (version: ProjectVersion, discard: boolean) =>
    run(`Opening ${label(version)}...`, async () => {
      setData(await openProjectVersion(projectId, version.id, discard));
      pushToast(`Now on ${label(version)} · ${version.name}`, "success");
      onSwitched();
    });

  function save(thenOpen?: ProjectVersion) {
    return run("Saving...", async () => {
      let saved = await saveProjectVersion(projectId, { name: name.trim() || null, note: note.trim() || null });
      const made = saved.versions.find((v) => v.id === saved.current_version_id);
      pushToast(`Saved as ${made ? `${label(made)} · ${made.name}` : "a new version"}`, "success");
      if (thenOpen) {
        saved = await openProjectVersion(projectId, thenOpen.id, false);
        pushToast(`Now on ${label(thenOpen)} · ${thenOpen.name}`, "success");
        onSwitched();
      }
      setData(saved);
    });
  }

  const rename = (version: ProjectVersion) =>
    run("Saving...", async () => setData(await updateProjectVersion(projectId, version.id, { name: name.trim(), note: note.trim() || null })));

  const remove = (version: ProjectVersion) =>
    run("Deleting...", async () => {
      setData(await deleteProjectVersion(projectId, version.id));
      pushToast(`${label(version)} deleted`, "success");
    });

  const noun = projectType === "image" ? "images" : "slides";

  return (
    <Card className="p-space-lg flex flex-col gap-space-md" id="versions">
      <div className="flex items-start justify-between flex-wrap gap-space-sm">
        <div className="flex items-start gap-space-sm">
          <MaterialIcon name="history" className="text-on-surface-variant mt-0.5" />
          <div>
            <div className="flex items-center gap-space-sm flex-wrap">
              <span className="font-headline-md text-headline-md">Versions</span>
              {current && (
                <span className="px-space-sm py-0.5 rounded-full text-label-sm font-mono bg-primary-container text-on-primary-container">
                  On {label(current)} · {current.name}
                </span>
              )}
              {versions.length > 0 && unsaved && (
                <span className="px-space-sm py-0.5 rounded-full text-label-sm font-headline-sm bg-amber-100 text-amber-800">Unsaved changes</span>
              )}
            </div>
            <div className="text-body-sm text-on-surface-variant">
              {versions.length === 0
                ? `Save the project as it is now -- its ${noun}, ${projectType === "image" ? "" : "tissue, patches, "}annotations and settings -- as a version you can come back to.`
                : !current
                  ? "The work is not saved as a version. Save it, or open a version below."
                  : unsaved
                    ? `The project has changed since ${label(current)}. Save the changes as a new version to keep them; ${label(current)} stays as it is.`
                    : `Everything is saved in ${label(current)}. Opening another version shows only what that version holds.`}
            </div>
          </div>
        </div>
        {canManage && (
          <div className="flex items-center gap-space-sm">
            {current && unsaved && (
              <Button icon="undo" onClick={() => openDialog({ kind: "discard", version: current })} disabled={!!busy}>
                Discard changes
              </Button>
            )}
            <Button variant={unsaved ? "primary" : "secondary"} icon="save" onClick={() => openDialog({ kind: "save" })} disabled={!!busy}>
              Save as new version
            </Button>
          </div>
        )}
      </div>

      {versions.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-body-md">
            <thead className="bg-surface-container-low text-label-md text-on-surface-variant">
              <tr>
                <th className="text-left px-space-md py-space-sm">Version</th>
                <th className="text-left px-space-md py-space-sm">Holds</th>
                <th className="text-left px-space-md py-space-sm">Made from</th>
                <th className="text-left px-space-md py-space-sm">Saved</th>
                {canManage && <th className="text-left px-space-md py-space-sm">Open</th>}
              </tr>
            </thead>
            <tbody>
              {versions.map((v) => {
                const isCurrent = v.id === data.current_version_id;
                const parent = v.parent_id != null ? byId.get(v.parent_id) : undefined;
                return (
                  <tr key={v.id} className={`border-t border-outline-variant ${isCurrent ? "bg-primary-container/20" : "hover:bg-surface-container-low/50"}`}>
                    <td className="px-space-md py-space-sm">
                      <div className="flex items-center gap-space-sm">
                        <span className="font-mono text-label-md text-secondary">{label(v)}</span>
                        <span className="font-headline-sm text-headline-sm break-all">{v.name}</span>
                      </div>
                      {v.note && <div className="text-body-sm text-on-surface-variant whitespace-pre-wrap">{v.note}</div>}
                    </td>
                    <td className="px-space-md py-space-sm font-mono text-label-md">{contents(v, projectType)}</td>
                    <td className="px-space-md py-space-sm font-mono text-label-md">{parent ? label(parent) : "--"}</td>
                    <td className="px-space-md py-space-sm text-body-sm text-on-surface-variant">
                      {when(v.created_at)}
                      {v.created_by ? ` · ${v.created_by}` : ""}
                    </td>
                    {canManage && (
                      <td className="px-space-md py-space-sm">
                        <div className="flex items-center gap-0.5">
                          {isCurrent ? (
                            <span className="text-label-md text-on-surface-variant w-[88px]">Open now</span>
                          ) : (
                            <Button
                              className="w-[88px] justify-center"
                              onClick={() => (unsaved ? openDialog({ kind: "unsaved", version: v }) : open(v, false))}
                              disabled={!!busy}
                              title={`Show the project as it was saved in ${label(v)}, and go on from there`}
                            >
                              Open
                            </Button>
                          )}
                          <IconButton icon="edit" onClick={() => openDialog({ kind: "rename", version: v })} disabled={!!busy} title="Rename, or change the note" />
                          <IconButton icon="delete" onClick={() => openDialog({ kind: "delete", version: v })} disabled={!!busy} title="Delete this version" className="hover:!text-error" />
                        </div>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {busy && !dialog && <div className="text-body-sm text-on-surface-variant">{busy}</div>}

      {/* Save as new version / rename */}
      <Modal open={dialog?.kind === "save" || dialog?.kind === "rename"} onClose={() => !busy && setDialog(null)} widthClass="max-w-lg">
        {(dialog?.kind === "save" || dialog?.kind === "rename") && (
          <div className="p-space-lg flex flex-col gap-space-md">
            <h2 className="font-headline-md text-headline-md">
              {dialog.kind === "rename" ? `Rename ${label(dialog.version)}` : `Save as new version (v${nextNumber})`}
            </h2>
            {dialog.kind === "save" && (
              <div className="text-body-md text-on-surface-variant">
                Saves the project exactly as it is now: its {noun}, {projectType === "image" ? "" : "tissue and patches, "}every annotation and
                suggestion, the classes and the settings. {current ? `${label(current)} stays as it is.` : ""}
                {dialog.thenOpen ? ` ${label(dialog.thenOpen)} is opened afterwards.` : ""}
              </div>
            )}
            <Field label="Name">
              <input
                className="input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={dialog.kind === "save" ? `Version ${nextNumber}` : ""}
                maxLength={200}
                autoFocus
              />
            </Field>
            <Field label="Note (optional)">
              <textarea
                className="input h-20 resize-none"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="What changed, e.g. the model used for predictions"
                maxLength={2000}
              />
            </Field>
            <div className="flex justify-end gap-space-sm">
              <Button variant="ghost" onClick={() => setDialog(null)} disabled={!!busy}>
                Cancel
              </Button>
              {dialog.kind === "rename" ? (
                <Button variant="primary" icon="save" onClick={() => rename(dialog.version)} disabled={!!busy || !name.trim()}>
                  {busy ?? "Save"}
                </Button>
              ) : (
                <Button variant="primary" icon="save" onClick={() => save(dialog.thenOpen)} disabled={!!busy}>
                  {busy ?? (dialog.thenOpen ? `Save, then open ${label(dialog.thenOpen)}` : "Save version")}
                </Button>
              )}
            </div>
          </div>
        )}
      </Modal>

      {/* Opening another version with unsaved changes / putting the current one back */}
      <Modal open={dialog?.kind === "unsaved" || dialog?.kind === "discard"} onClose={() => !busy && setDialog(null)} widthClass="max-w-lg">
        {(dialog?.kind === "unsaved" || dialog?.kind === "discard") && (
          <div className="p-space-lg flex flex-col gap-space-md">
            <div className="flex items-center gap-space-sm">
              <span className="w-10 h-10 rounded-full bg-amber-100 flex items-center justify-center shrink-0">
                <MaterialIcon name="warning" className="text-amber-800" />
              </span>
              <h2 className="font-headline-md text-headline-md">
                {dialog.kind === "discard" ? "Discard the unsaved changes?" : "You have unsaved changes"}
              </h2>
            </div>
            <div className="text-body-md text-on-surface-variant">
              {dialog.kind === "discard" ? (
                <>
                  The project goes back to exactly what <strong>{label(dialog.version)} · {dialog.version.name}</strong> holds. Everything done
                  since -- annotations, {noun} added or removed, patches, settings -- is lost. This cannot be undone.
                </>
              ) : (
                <>
                  Opening <strong>{label(dialog.version)} · {dialog.version.name}</strong> replaces what the project shows with that version.
                  What you changed since {current ? label(current) : "the start"} is not in any version yet: save it as a new version to keep
                  it, or it is lost.
                </>
              )}
            </div>
            <div className="flex justify-end gap-space-sm flex-wrap">
              <Button variant="ghost" onClick={() => setDialog(null)} disabled={!!busy}>
                Cancel
              </Button>
              <Button variant="danger" onClick={() => open(dialog.version, true)} disabled={!!busy}>
                {busy ?? (dialog.kind === "discard" ? "Discard changes" : `Discard changes and open ${label(dialog.version)}`)}
              </Button>
              {dialog.kind === "unsaved" && (
                <Button variant="primary" icon="save" onClick={() => openDialog({ kind: "save", thenOpen: dialog.version })} disabled={!!busy}>
                  Save as new version first
                </Button>
              )}
            </div>
          </div>
        )}
      </Modal>

      <ConfirmDeleteModal
        key={dialog?.kind === "delete" ? dialog.version.id : "none"}
        open={dialog?.kind === "delete"}
        onClose={() => setDialog(null)}
        onConfirm={() => (dialog?.kind === "delete" ? remove(dialog.version) : undefined)}
        busy={!!busy}
        title="Delete this version?"
        confirmPhrase={dialog?.kind === "delete" ? label(dialog.version) : ""}
        description={
          dialog?.kind === "delete" ? (
            <>
              This permanently deletes <strong>{label(dialog.version)} · {dialog.version.name}</strong>: it can no longer be opened, and the
              files of {noun} that only it still had are removed. The project as it is now is not changed
              {dialog.version.id === data.current_version_id ? ", but it will count as unsaved" : ""}. This cannot be undone.
            </>
          ) : null
        }
      />
    </Card>
  );
}
