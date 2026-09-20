import { useEffect, useState } from "react";
import { Field } from "../../components/formControls";
import { MaterialIcon } from "../../components/MaterialIcon";
import { Button, Modal } from "../../components/primitives";
import { updateProject } from "../../services/api";
import { useUiStore } from "../../stores/uiStore";
import type { ProjectDetail } from "../../types/api";
import { ORGANS } from "./constants";

interface Props {
  open: boolean;
  onClose: () => void;
  project: ProjectDetail;
  onSaved: (project: ProjectDetail) => void;
}

/** Edits the project's descriptive details. The project ID (slug) is
 * deliberately read-only: it names the project in exports and in the delete
 * confirmation, so it must not drift after creation. */
export function EditProjectModal({ open, onClose, project, onSaved }: Props) {
  const pushToast = useUiStore((s) => s.pushToast);
  const [name, setName] = useState("");
  const [organ, setOrgan] = useState("");
  const [team, setTeam] = useState("");
  const [description, setDescription] = useState("");
  const [status, setStatus] = useState(project.status);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setName(project.name);
    setOrgan(project.organ ?? "");
    setTeam(project.team ?? "");
    setDescription(project.description ?? "");
    setStatus(project.status);
  }, [open, project]);

  const changed =
    name.trim() !== project.name ||
    organ !== (project.organ ?? "") ||
    team.trim() !== (project.team ?? "") ||
    description.trim() !== (project.description ?? "") ||
    status !== project.status;

  async function handleSave() {
    setBusy(true);
    try {
      const updated = await updateProject(project.id, {
        name: name.trim(),
        organ: organ || null,
        team: team.trim() || null,
        description: description.trim() || null,
        status,
      });
      pushToast("Project details saved", "success");
      onSaved(updated);
    } catch (e) {
      pushToast(e instanceof Error ? e.message : "Failed to save project", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} widthClass="max-w-lg">
      <div className="p-space-lg flex flex-col gap-space-md">
        <div className="flex items-center justify-between">
          <h2 className="font-headline-md text-headline-md">Project details</h2>
          <button onClick={onClose} aria-label="Close">
            <MaterialIcon name="close" />
          </button>
        </div>
        <Field label="Project ID">
          <input className="input font-mono bg-surface-container" value={project.slug} disabled />
        </Field>
        <Field label="Project name">
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <div className="grid grid-cols-2 gap-space-md">
          <Field label="Primary organ">
            <select className="input" value={organ} onChange={(e) => setOrgan(e.target.value)}>
              <option value="">--</option>
              {[...new Set([...ORGANS, ...(project.organ ? [project.organ] : [])])].map((o) => (
                <option key={o}>{o}</option>
              ))}
            </select>
          </Field>
          <Field label="Status">
            <select className="input" value={status} onChange={(e) => setStatus(e.target.value as ProjectDetail["status"])}>
              <option value="active">Active</option>
              <option value="review">Review phase</option>
              <option value="completed">Completed</option>
            </select>
          </Field>
        </div>
        <Field label="Researcher / team">
          <input className="input" value={team} onChange={(e) => setTeam(e.target.value)} />
        </Field>
        <Field label="Description">
          <textarea className="input h-24 resize-none" value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <div className="flex justify-end gap-space-sm">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" icon="save" onClick={handleSave} disabled={busy || !changed || !name.trim()}>
            {busy ? "Saving..." : "Save"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
