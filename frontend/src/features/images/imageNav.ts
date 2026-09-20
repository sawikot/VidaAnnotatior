import type { ImageSummary, PatchStatus } from "../../types/api";

export type ImageFilter = "any" | "unannotated" | "flagged";

const DONE: PatchStatus[] = ["annotated", "reviewed"];

function matches(image: ImageSummary, filter: ImageFilter): boolean {
  if (filter === "unannotated") return image.status === "unannotated";
  if (filter === "flagged") return image.flagged;
  return true;
}

/**
 * The image to move to from `currentSlideId`, in list order, or null when there is
 * none in that direction. "Next unannotated" / "next flagged" skip over images that
 * don't match. If the current image isn't in the list (e.g. it was just removed) the
 * search starts from the top / bottom.
 */
export function neighbour(
  images: ImageSummary[],
  currentSlideId: number,
  direction: "next" | "prev",
  filter: ImageFilter = "any",
): ImageSummary | null {
  const at = images.findIndex((i) => i.slide_id === currentSlideId);
  const step = direction === "next" ? 1 : -1;
  let i = at === -1 ? (direction === "next" ? 0 : images.length - 1) : at + step;
  for (; i >= 0 && i < images.length; i += step) {
    if (matches(images[i], filter)) return images[i];
  }
  return null;
}

/** Where to start annotating: the first image still to do, else the first image. */
export function firstToAnnotate(images: ImageSummary[]): ImageSummary | null {
  return images.find((i) => i.status === "unannotated") ?? images[0] ?? null;
}

export function progress(images: ImageSummary[]): { total: number; done: number } {
  return { total: images.length, done: images.filter((i) => DONE.includes(i.status)).length };
}
