import { useState, type ReactNode } from "react";
import { ConfirmDeleteModal } from "../../components/primitives";
import { deleteSlide } from "../../services/api";
import { useUiStore } from "../../stores/uiStore";

export interface DeleteTarget {
  id: number;
  filename: string;
  /** What will be lost with it, e.g. "12 annotated patches". */
  detail?: ReactNode;
}

/** Confirms, then deletes one slide or image with everything under it (patches, annotations,
 * tissue masks, stored files). Typing the file name unlocks the button. */
export function DeleteSlideModal({
  target,
  noun,
  onClose,
  onDeleted,
}: {
  target: DeleteTarget | null;
  noun: "slide" | "image";
  onClose: () => void;
  onDeleted: () => void;
}) {
  const pushToast = useUiStore((s) => s.pushToast);
  const [busy, setBusy] = useState(false);

  async function confirm() {
    if (!target) return;
    setBusy(true);
    try {
      await deleteSlide(target.id);
      pushToast(`${target.filename} deleted`, "success");
      onClose();
      onDeleted();
    } catch (e) {
      pushToast(e instanceof Error ? e.message : `Failed to delete the ${noun}`, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <ConfirmDeleteModal
      key={target?.id} // a fresh, empty confirm box for each target
      open={!!target}
      onClose={onClose}
      onConfirm={confirm}
      busy={busy}
      title={`Delete this ${noun}?`}
      confirmPhrase={target?.filename ?? ""}
      description={
        <>
          This permanently deletes <strong className="break-all">{target?.filename}</strong> and everything under it --
          {noun === "slide" ? " its tissue masks, patches and" : ""} every annotation{target?.detail ? <> ({target.detail})</> : null}.
          The {noun} file stored by the app is removed too. This cannot be undone.
        </>
      }
    />
  );
}
