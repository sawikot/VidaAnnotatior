import { ApiError, UploadAborted, type UploadItem } from "../../services/api";
import type { SlideBatchImportResult } from "../../types/api";

/** How one upload request (one slide, or a pack of small files) ended. */
export type BatchOutcome =
  | { kind: "ok"; result: SlideBatchImportResult }
  /** Could not be uploaded; `reason` says why in words a user can act on. */
  /** `fatal`: the server can't take anything more (its disk is full), so the rest would fail too. */
  | { kind: "failed"; reason: string; retried: boolean; fatal?: boolean }
  | { kind: "cancelled" };

export interface StartedUpload {
  promise: Promise<SlideBatchImportResult>;
  abort: () => void;
}

export interface SendOptions {
  items: UploadItem[];
  /** Starts one attempt (uploadSlides, bound to the project). */
  start: (hooks: { onProgress: (sent: number) => void; onSent: () => void }) => StartedUpload;
  signal: AbortSignal;
  onProgress: (sent: number) => void;
  onSent: () => void;
  /** Shown while waiting to try again, e.g. "No data got through for 60 s -- trying again (2 of 3) in 5 s". */
  onNote: (note: string | null) => void;
  attempts?: number;
  /** No bytes sent for this long means the upload is stuck. */
  stallMs?: number;
  /** All bytes sent, and no answer for this long. */
  serverMs?: number;
  retryDelaysMs?: number[];
}

const RETRYABLE_STATUS = new Set([502, 503, 504]); // a proxy or a restarting server: worth another go

/** The first file the browser can no longer read (moved, deleted, locked, online-only), if any. */
export async function findUnreadable(items: UploadItem[]): Promise<string | null> {
  for (const { file, path } of items) {
    try {
      await file.slice(0, 1).arrayBuffer();
    } catch {
      return path;
    }
  }
  return null;
}

const seconds = (ms: number) => (ms >= 60_000 && ms % 60_000 === 0 ? `${ms / 60_000} min` : `${Math.round(ms / 1000)} s`);

/**
 * Uploads one batch, watching it: if no bytes go out for `stallMs` the request is cancelled and
 * tried again (up to `attempts` times), so a stuck upload can't hold the dialog forever. Every way
 * it can end is turned into an outcome with a reason, never a thrown error.
 */
export async function sendBatch({
  items,
  start,
  signal,
  onProgress,
  onSent,
  onNote,
  attempts = 3,
  stallMs = 60_000,
  serverMs = 30 * 60_000,
  retryDelaysMs = [5_000, 15_000],
}: SendOptions): Promise<BatchOutcome> {
  let lastReason = "";
  for (let attempt = 1; attempt <= attempts; attempt++) {
    if (signal.aborted) return { kind: "cancelled" };

    const unreadable = await findUnreadable(items);
    if (unreadable) {
      return {
        kind: "failed",
        retried: attempt > 1,
        reason:
          `your browser can't read ${unreadable}. It may have been moved, renamed or deleted, be open in another program, ` +
          "or be an online-only (cloud) file -- make it available on this computer and upload it again.",
      };
    }

    const outcome = await attemptOnce(start, signal, onProgress, onSent, stallMs, serverMs);
    if (outcome.kind !== "retry") return outcome;
    lastReason = outcome.reason;

    if (attempt < attempts) {
      const delay = retryDelaysMs[Math.min(attempt - 1, retryDelaysMs.length - 1)] ?? 0;
      onProgress(0);
      onNote(`${capitalize(lastReason)} -- trying again (${attempt + 1} of ${attempts}) in ${seconds(delay)}...`);
      if (!(await wait(delay, signal))) return { kind: "cancelled" };
      onNote(null);
    }
  }
  onNote(null);
  return {
    kind: "failed",
    retried: true,
    reason:
      `${lastReason} (tried ${attempts} times). The server may have stopped accepting data -- check its free disk ` +
      "space and its logs -- or the network connection is dropping.",
  };
}

type AttemptOutcome = BatchOutcome | { kind: "retry"; reason: string };

async function attemptOnce(
  start: SendOptions["start"],
  signal: AbortSignal,
  onProgress: (sent: number) => void,
  onSent: () => void,
  stallMs: number,
  serverMs: number,
): Promise<AttemptOutcome> {
  let lastActivity = Date.now();
  let sentHighWater = 0;
  let serverWork = false;
  let gaveUp = null as "stall" | "server" | null; // set by the watchdog, read once the request ends

  const upload = start({
    onProgress: (sent) => {
      if (sent > sentHighWater) {
        sentHighWater = sent;
        lastActivity = Date.now();
      }
      onProgress(sent);
    },
    onSent: () => {
      serverWork = true;
      lastActivity = Date.now();
      onSent();
    },
  });
  const cancel = () => upload.abort();
  signal.addEventListener("abort", cancel);
  const watchdog = setInterval(() => {
    const idle = Date.now() - lastActivity;
    if (!serverWork && idle > stallMs) gaveUp = "stall";
    else if (serverWork && idle > serverMs) gaveUp = "server";
    if (gaveUp) upload.abort();
  }, 1_000);

  try {
    return { kind: "ok", result: await upload.promise };
  } catch (e) {
    if (gaveUp === "stall") return { kind: "retry", reason: `no data got through for ${seconds(stallMs)}` };
    if (gaveUp === "server") {
      // Not retried: the server may still be importing it, and a second copy would be a duplicate.
      return {
        kind: "failed",
        retried: false,
        reason: `the server received it but gave no answer for ${seconds(serverMs)}. It may still appear -- reload the page in a while.`,
      };
    }
    if (e instanceof UploadAborted || signal.aborted) return { kind: "cancelled" };
    if (e instanceof ApiError) {
      if (RETRYABLE_STATUS.has(e.status)) return { kind: "retry", reason: `the server answered ${e.status} (${e.message})` };
      if (e.status === 507) return { kind: "failed", retried: false, fatal: true, reason: e.message }; // out of disk space
      return { kind: "failed", retried: false, reason: `upload refused: ${e.message}` };
    }
    return { kind: "retry", reason: "the connection to the server was lost" };
  } finally {
    clearInterval(watchdog);
    signal.removeEventListener("abort", cancel);
  }
}

function capitalize(s: string) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Resolves true after `ms`, or false as soon as the signal aborts. */
function wait(ms: number, signal: AbortSignal): Promise<boolean> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve(false);
    const onAbort = () => {
      clearTimeout(timer);
      resolve(false);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve(true);
    }, ms);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}
