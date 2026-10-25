import { afterEach, describe, expect, it, vi } from "vitest";
import { uploadSlides } from "./api";

/** Just enough of XMLHttpRequest to drive the upload's events by hand. */
class FakeXhr {
  static last: FakeXhr;
  upload: { onprogress?: (e: { lengthComputable: boolean; loaded: number; total: number }) => void; onload?: () => void } = {};
  constructor() {
    FakeXhr.last = this;
  }
  open() {}
  send() {}
  abort() {}
}

const file = { file: new File(["x"], "a.svs"), path: "a.svs" };

describe("uploadSlides: knowing when every byte is out", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("counts reaching 100% as sent, as Firefox fires upload load only once the server answers", () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const onSent = vi.fn();
    uploadSlides(1, [file], { onSent });
    const up = FakeXhr.last.upload;

    up.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 });
    expect(onSent).not.toHaveBeenCalled();
    up.onprogress?.({ lengthComputable: true, loaded: 100, total: 100 });
    expect(onSent).toHaveBeenCalledTimes(1);

    up.onload?.(); // Chrome's (and later Firefox's) load event: still only once
    expect(onSent).toHaveBeenCalledTimes(1);
  });

  it("still counts the load event when progress never reached 100%", () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const onSent = vi.fn();
    uploadSlides(1, [file], { onSent });
    FakeXhr.last.upload.onload?.();
    expect(onSent).toHaveBeenCalledTimes(1);
  });
});
