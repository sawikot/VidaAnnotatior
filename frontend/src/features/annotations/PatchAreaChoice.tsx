import { useState } from "react";
import { MaterialIcon } from "../../components/MaterialIcon";
import { Button, Modal } from "../../components/primitives";

/** Asked when patch view is chosen on a slide that has no patches yet: cut the whole slide now,
 *  or go and find the tissue first (Slide Processing), then come back. */
export function PatchAreaChoice({
  open,
  onClose,
  onWholeSlide,
  onTissue,
}: {
  open: boolean;
  onClose: () => void;
  /** Cut patches over the whole slide; resolves once they exist. */
  onWholeSlide: () => Promise<void>;
  onTissue: () => void;
}) {
  const [busy, setBusy] = useState(false);

  async function whole() {
    setBusy(true);
    try {
      await onWholeSlide();
    } finally {
      setBusy(false);
    }
  }

  const option =
    "flex items-start gap-space-md text-left p-space-md rounded-lg border border-outline-variant hover:border-primary hover:bg-surface-container-low disabled:opacity-50 disabled:cursor-wait";
  return (
    <Modal open={open} onClose={() => !busy && onClose()} widthClass="max-w-lg">
      <div className="p-space-lg flex flex-col gap-space-md">
        <div>
          <h2 className="font-headline-md text-headline-md">
            Make patches for this slide
          </h2>
          <p className="text-body-sm text-on-surface-variant">
            This slide has no patches yet. Which part of it should be cut into
            patches? It uses the project&apos;s patch size.
          </p>
        </div>
        <button className={option} onClick={() => void whole()} disabled={busy}>
          <MaterialIcon name="crop_free" className="text-primary mt-0.5" />
          <span>
            <span className="block text-body-md text-on-surface font-medium">
              {busy ? "Cutting the whole slide..." : "Full slide"}
            </span>
            <span className="block text-body-sm text-on-surface-variant">
              Every part of the slide, up to its edges, right now. Blank glass
              becomes patches too, so there are more of them.
            </span>
          </span>
        </button>
        <button className={option} onClick={onTissue} disabled={busy}>
          <MaterialIcon name="biotech" className="text-primary mt-0.5" />
          <span>
            <span className="block text-body-md text-on-surface font-medium">
              Tissue area only
            </span>
            <span className="block text-body-sm text-on-surface-variant">
              Opens Slide Processing to detect or draw the tissue and generate
              its patches; you come back here afterwards.
            </span>
          </span>
        </button>
        <div className="flex justify-end">
          <Button onClick={onClose} disabled={busy}>
            Cancel
          </Button>
        </div>
      </div>
    </Modal>
  );
}
