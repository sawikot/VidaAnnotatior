import type { UploadItem } from "../../services/api";

const LOOSE_SLIDE_EXTENSIONS = new Set([".svs", ".tif", ".tiff", ".ndpi", ".scn", ".bif", ".svslide", ".vms", ".vmu", ".mrxs"]);
/** File types an image project accepts (kept in step with the server's IMAGE_EXTENSIONS). */
export const IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"];

const VMS_COMPANIONS = new Set([".jpg", ".jpeg", ".opt", ".ngr"]);

export function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot < 0 ? "" : name.slice(dot).toLowerCase();
}

function baseName(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

function dirName(path: string): string {
  const i = path.lastIndexOf("/");
  return i < 0 ? "" : path.slice(0, i);
}

/** Wraps a picked File. For folder picks the browser exposes the path inside
 * the folder as `webkitRelativePath`; otherwise it is just the file name. */
export function toUploadItem(file: File): UploadItem {
  const rel = (file as File & { webkitRelativePath?: string }).webkitRelativePath;
  return { file, path: rel || file.name };
}

/** Adds newly picked files, ignoring ones already selected (same path and size)
 * so picking the same folder twice doesn't upload everything twice. */
export function mergeSelections(existing: UploadItem[], incoming: UploadItem[]): UploadItem[] {
  const seen = new Set(existing.map((i) => `${i.path.toLowerCase()}|${i.file.size}`));
  const merged = [...existing];
  for (const item of incoming) {
    const key = `${item.path.toLowerCase()}|${item.file.size}`;
    if (!seen.has(key)) {
      seen.add(key);
      merged.push(item);
    }
  }
  return merged;
}

export function summarize(items: UploadItem[]) {
  let bytes = 0;
  let archives = 0;
  let slideFiles = 0;
  for (const { file, path } of items) {
    bytes += file.size;
    const ext = extensionOf(path);
    if (ext === ".zip") archives += 1;
    else if (LOOSE_SLIDE_EXTENSIONS.has(ext)) slideFiles += 1;
  }
  return { count: items.length, bytes, archives, slideFiles };
}

/** Problems that can be spotted before uploading anything: multi-file formats
 * whose companion files aren't in the selection. The server checks again and
 * gives the authoritative answer; this just saves a long upload that is
 * certain to be refused. */
export function findProblems(items: UploadItem[]): string[] {
  const problems: string[] = [];
  const lower = items.map((i) => i.path.toLowerCase());

  for (const { path } of items) {
    const ext = extensionOf(path);
    const name = baseName(path);
    const stem = name.slice(0, name.length - ext.length);
    const dir = dirName(path);

    if (ext === ".mrxs") {
      const folder = `${dir ? dir + "/" : ""}${stem}/`.toLowerCase();
      if (!lower.some((p) => p.startsWith(folder))) {
        problems.push(
          `${name}: its data folder "${stem}/" isn't in your selection. Use "Choose folder" on the folder that holds both, or zip them together.`,
        );
      }
    } else if (ext === ".vms" || ext === ".vmu") {
      const prefix = `${dir ? dir + "/" : ""}${stem}`.toLowerCase();
      const hasTiles = items.some(
        (i) => i.path !== path && i.path.toLowerCase().startsWith(prefix) && VMS_COMPANIONS.has(extensionOf(i.path)),
      );
      if (!hasTiles) problems.push(`${name}: its image tile files (${stem}*.jpg / .ngr) aren't in your selection.`);
    }
  }
  return problems;
}

const JUNK_NAMES = new Set([".ds_store", "thumbs.db", "desktop.ini"]);

/** Files operating systems add to folders and that are never slides (kept in step with the server's is_junk). */
function isJunk(path: string): boolean {
  const parts = path.toLowerCase().split("/");
  const name = parts[parts.length - 1];
  return parts.includes("__macosx") || JUNK_NAMES.has(name) || name.startsWith("._");
}

/** One upload request: whole slides only (a .mrxs never travels without its data folder). */
export interface UploadBatch {
  items: UploadItem[];
  bytes: number;
  /** The main file of each slide/image/zip in the batch, for progress and error messages. */
  names: string[];
}

export interface UploadPlan {
  batches: UploadBatch[];
  /** Selected files that can't be part of any slide/image; they are not sent at all. */
  ignored: number;
}

/**
 * Splits a selection into upload requests, so each slide is imported as soon as its own bytes have
 * arrived instead of after the whole folder, and a failure costs one request rather than everything.
 *
 * A unit is one slide with its companions (a .mrxs plus its data folder, a .vms/.vmu plus its tile
 * files), one .zip, or one image. Small units are packed together up to `maxBatchBytes` /
 * `maxBatchFiles` so a folder of thousands of small images isn't thousands of requests. The server
 * still decides what is really a slide; this only has to keep companions in the same request.
 */
export function planUploads(
  items: UploadItem[],
  isImage: boolean,
  { maxBatchBytes = 256 * 1024 * 1024, maxBatchFiles = 500 }: { maxBatchBytes?: number; maxBatchFiles?: number } = {},
): UploadPlan {
  const lower = items.map((i) => i.path.toLowerCase());
  const claimed = new Set<number>();
  const units: number[][] = [];

  const claim = (indices: number[]) => {
    const fresh = indices.filter((i) => !claimed.has(i));
    fresh.forEach((i) => claimed.add(i));
    if (fresh.length) units.push(fresh);
  };

  items.forEach((item, i) => {
    if (isJunk(item.path)) claimed.add(i);
  });

  if (!isImage) {
    // Multi-file slides first, so a .tif inside a .mrxs data folder goes with that slide.
    items.forEach(({ path }, i) => {
      const ext = extensionOf(path);
      if (claimed.has(i) || (ext !== ".mrxs" && ext !== ".vms" && ext !== ".vmu")) return;
      const name = baseName(path);
      const stem = name.slice(0, name.length - ext.length).toLowerCase();
      const dir = dirName(path).toLowerCase();
      const folder = `${dir ? dir + "/" : ""}${stem}/`;
      const members = [i];
      lower.forEach((p, j) => {
        if (j === i || claimed.has(j) || isJunk(p)) return;
        if (p.startsWith(folder)) members.push(j);
        else if (ext !== ".mrxs" && dirName(p) === dir && baseName(p).startsWith(stem) && VMS_COMPANIONS.has(extensionOf(p))) members.push(j);
      });
      claim(members);
    });
  }

  items.forEach(({ path }, i) => {
    const ext = extensionOf(path);
    const wanted = ext === ".zip" || (isImage ? IMAGE_EXTENSIONS.includes(ext) : LOOSE_SLIDE_EXTENSIONS.has(ext));
    if (!claimed.has(i) && wanted) claim([i]);
  });

  const ignored = items.filter((it, i) => !claimed.has(i) && !isJunk(it.path)).length;

  const batches: UploadBatch[] = [];
  let current: UploadBatch | null = null;
  for (const unit of units) {
    const unitItems = unit.map((i) => items[i]);
    const unitBytes = unitItems.reduce((n, it) => n + it.file.size, 0);
    if (current && (current.bytes + unitBytes > maxBatchBytes || current.items.length + unitItems.length > maxBatchFiles)) {
      batches.push(current);
      current = null;
    }
    current ??= { items: [], bytes: 0, names: [] };
    current.items.push(...unitItems);
    current.bytes += unitBytes;
    current.names.push(baseName(unitItems[0].path));
  }
  if (current) batches.push(current);
  return { batches, ignored };
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}
