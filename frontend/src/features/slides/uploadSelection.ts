import type { UploadItem } from "../../services/api";

const LOOSE_SLIDE_EXTENSIONS = new Set([".svs", ".tif", ".tiff", ".ndpi", ".scn", ".bif", ".svslide", ".vms", ".vmu", ".mrxs"]);
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
