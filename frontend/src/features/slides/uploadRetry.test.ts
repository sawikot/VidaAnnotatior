import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, UploadAborted, type UploadItem } from "../../services/api";
import type { SlideBatchImportResult } from "../../types/api";
import { sendBatch, type SendOptions, type StartedUpload } from "./uploadRetry";

const RESULT: SlideBatchImportResult = { slides: [], skipped: [], ignored_file_count: 0, warnings: [] };
const items: UploadItem[] = [{ file: new File(["abc"], "a.svs"), path: "F/a.svs" }];

/** A fake request the test drives by hand: send bytes, finish, fail -- or just hang. */
function fakeUpload() {
  let resolve!: (r: SlideBatchImportResult) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<SlideBatchImportResult>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  const hooks: { onProgress: (sent: number) => void; onSent: () => void } = { onProgress: () => {}, onSent: () => {} };
  const upload: StartedUpload & { aborted: boolean } = {
    promise,
    aborted: false,
    abort() {
      this.aborted = true;
      reject(new UploadAborted());
    },
  };
  return { upload, hooks, resolve, reject };
}

function run(attemptsBehaviour: ((f: ReturnType<typeof fakeUpload>) => void)[], extra: Partial<SendOptions> = {}) {
  const started: ReturnType<typeof fakeUpload>[] = [];
  const notes: (string | null)[] = [];
  const controller = new AbortController();
  const outcome = sendBatch({
    items,
    signal: controller.signal,
    start: (hooks) => {
      const f = fakeUpload();
      Object.assign(f.hooks, hooks);
      started.push(f);
      attemptsBehaviour[started.length - 1]?.(f);
      return f.upload;
    },
    onProgress: () => {},
    onSent: () => {},
    onNote: (n) => notes.push(n),
    ...extra,
  });
  return { outcome, started, notes, controller };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("sendBatch", () => {
  it("returns the server's answer when the upload goes through", async () => {
    const { outcome } = run([(f) => f.resolve(RESULT)]);
    await expect(outcome).resolves.toEqual({ kind: "ok", result: RESULT });
  });

  it("cancels an upload that stops sending, tries again, and says why when it never gets through", async () => {
    const { outcome, started, notes } = run([(f) => f.hooks.onProgress(100)]); // then nothing more, every time
    await vi.advanceTimersByTimeAsync(3 * 62_000 + 20_000);
    const res = await outcome;
    expect(started).toHaveLength(3);
    expect(started.every((f) => f.upload.aborted)).toBe(true);
    expect(res).toMatchObject({ kind: "failed", retried: true });
    expect(res.kind === "failed" && res.reason).toMatch(/no data got through for 1 min \(tried 3 times\).*disk space/);
    expect(notes).toContain("No data got through for 1 min -- trying again (2 of 3) in 5 s...");
  });

  it("keeps an upload that is slow but still moving", async () => {
    const { outcome, started } = run([
      (f) => {
        for (let t = 1; t <= 5; t++) setTimeout(() => f.hooks.onProgress(t * 10), t * 50_000); // a trickle, 50 s apart
        setTimeout(() => f.resolve(RESULT), 260_000);
      },
    ]);
    await vi.advanceTimersByTimeAsync(261_000);
    await expect(outcome).resolves.toMatchObject({ kind: "ok" });
    expect(started).toHaveLength(1);
  });

  it("succeeds on a later attempt after a stall", async () => {
    const { outcome, started } = run([() => {}, (f) => f.resolve(RESULT)]);
    await vi.advanceTimersByTimeAsync(62_000 + 5_000);
    await expect(outcome).resolves.toMatchObject({ kind: "ok" });
    expect(started).toHaveLength(2);
  });

  it("does not retry when the server refuses the upload", async () => {
    const { outcome, started } = run([(f) => f.reject(new ApiError(413, "Upload is larger than the configured size limit"))]);
    await expect(outcome).resolves.toEqual({ kind: "failed", retried: false, reason: "upload refused: Upload is larger than the configured size limit" });
    expect(started).toHaveLength(1);
  });

  it("stops without retrying when the server is out of disk space", async () => {
    const full = "Not enough disk space on the server: 1.2 GB free, and this upload needs 3.1 GB";
    const { outcome, started } = run([(f) => f.reject(new ApiError(507, full))]);
    await expect(outcome).resolves.toEqual({ kind: "failed", retried: false, fatal: true, reason: full });
    expect(started).toHaveLength(1);
  });

  it("retries a lost connection and a gateway error", async () => {
    const { outcome, started } = run([(f) => f.reject(new Error("Network error")), (f) => f.reject(new ApiError(502, "Bad Gateway")), (f) => f.resolve(RESULT)]);
    await vi.advanceTimersByTimeAsync(5_000 + 15_000);
    await expect(outcome).resolves.toMatchObject({ kind: "ok" });
    expect(started).toHaveLength(3);
  });

  it("does not resend a slide the server received but has not answered for", async () => {
    const { outcome, started } = run([(f) => f.hooks.onSent()], { serverMs: 10 * 60_000 });
    await vi.advanceTimersByTimeAsync(10 * 60_000 + 2_000);
    const res = await outcome;
    expect(started).toHaveLength(1);
    expect(res.kind === "failed" && res.reason).toMatch(/gave no answer for 10 min. It may still appear/);
  });

  it("reports a file the browser can no longer read, without sending anything", async () => {
    const bad = new File(["x"], "b.dat");
    vi.spyOn(bad, "slice").mockReturnValue({ arrayBuffer: () => Promise.reject(new Error("NotReadableError")) } as unknown as Blob);
    const started: unknown[] = [];
    const res = await sendBatch({
      items: [...items, { file: bad, path: "F/S/b.dat" }],
      signal: new AbortController().signal,
      start: () => {
        started.push(1);
        return fakeUpload().upload;
      },
      onProgress: () => {},
      onSent: () => {},
      onNote: () => {},
    });
    expect(started).toHaveLength(0);
    expect(res.kind === "failed" && res.reason).toMatch(/your browser can't read F\/S\/b\.dat.*online-only/);
  });

  it("stops at once when cancelled, also while waiting to retry", async () => {
    const { outcome, started, controller } = run([() => {}]);
    await vi.advanceTimersByTimeAsync(62_000); // stalled, now waiting 5 s before the second try
    controller.abort();
    await expect(outcome).resolves.toEqual({ kind: "cancelled" });
    expect(started).toHaveLength(1);
  });
});
