import { describe, expect, it } from "vitest";
import type { UploadItem } from "../../services/api";
import { findProblems, formatBytes, mergeSelections, summarize, toUploadItem } from "./uploadSelection";

const item = (path: string, size = 10): UploadItem => ({ file: new File([new Uint8Array(size)], path.split("/").pop()!), path });

describe("toUploadItem", () => {
  it("uses the folder-relative path when the browser provides one, else the name", () => {
    const withPath = new File(["x"], "Slide.mrxs");
    Object.defineProperty(withPath, "webkitRelativePath", { value: "Case/Slide.mrxs" });
    expect(toUploadItem(withPath).path).toBe("Case/Slide.mrxs");
    expect(toUploadItem(new File(["x"], "a.svs")).path).toBe("a.svs");
  });
});

describe("mergeSelections", () => {
  it("ignores files that are already selected", () => {
    const first = [item("a.svs", 5), item("F/b.tif", 7)];
    const merged = mergeSelections(first, [item("A.SVS", 5), item("F/b.tif", 7), item("c.ndpi", 9)]);
    expect(merged.map((i) => i.path)).toEqual(["a.svs", "F/b.tif", "c.ndpi"]);
  });
  it("keeps same-named files of different size (different content)", () => {
    expect(mergeSelections([item("a.svs", 5)], [item("a.svs", 6)])).toHaveLength(2);
  });
});

describe("summarize", () => {
  it("counts files, bytes, archives and slide files", () => {
    const s = summarize([item("a.svs", 100), item("pack.ZIP", 50), item("Slide/Data0000.dat", 25), item("x.tif", 1)]);
    expect(s).toEqual({ count: 4, bytes: 176, archives: 1, slideFiles: 2 });
  });
});

describe("findProblems", () => {
  it("flags a .mrxs with no data folder in the selection", () => {
    const problems = findProblems([item("Slide1.mrxs")]);
    expect(problems).toHaveLength(1);
    expect(problems[0]).toMatch(/Slide1\.mrxs.*Slide1\/.*Choose folder/);
  });

  it("accepts a .mrxs whose folder was picked with it, at any depth, case-insensitively", () => {
    expect(findProblems([item("Case/Slide1.mrxs"), item("Case/Slide1/Slidedat.ini"), item("Case/Slide1/Data0000.dat")])).toEqual([]);
    expect(findProblems([item("S.mrxs"), item("s/Slidedat.ini")])).toEqual([]);
  });

  it("does not accept a same-named folder somewhere else", () => {
    expect(findProblems([item("A/Slide1.mrxs"), item("B/Slide1/Slidedat.ini")])).toHaveLength(1);
  });

  it("checks VMS/VMU tile files but leaves single-file formats alone", () => {
    expect(findProblems([item("CMU-1.vms")])).toHaveLength(1);
    expect(findProblems([item("CMU-1.vms"), item("CMU-1(0,0).jpg")])).toEqual([]);
    expect(findProblems([item("a.svs"), item("b.ndpi"), item("c.tif"), item("pack.zip")])).toEqual([]);
  });
});

describe("formatBytes", () => {
  it("formats sizes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(5 * 1024 ** 3)).toBe("5.0 GB");
  });
});
