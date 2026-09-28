import { describe, expect, it } from "vitest";
import type { UploadItem } from "../../services/api";
import { findProblems, formatBytes, mergeSelections, planUploads, summarize, toUploadItem } from "./uploadSelection";

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

describe("planUploads", () => {
  const paths = (plan: ReturnType<typeof planUploads>) => plan.batches.map((b) => b.items.map((i) => i.path));
  const big = 300 * 1024 * 1024;
  const sized = (path: string, size: number): UploadItem => {
    const it = item(path, 0);
    Object.defineProperty(it.file, "size", { value: size });
    return it;
  };

  it("sends each large slide in its own request", () => {
    const plan = planUploads([sized("F/a.svs", big), sized("F/b.ndpi", big)], false);
    expect(paths(plan)).toEqual([["F/a.svs"], ["F/b.ndpi"]]);
    expect(plan.batches.map((b) => b.names)).toEqual([["a.svs"], ["b.ndpi"]]);
  });

  it("packs small slides together", () => {
    expect(paths(planUploads([item("F/a.svs"), item("F/b.svs"), item("F/c.zip")], false))).toEqual([["F/a.svs", "F/b.svs", "F/c.zip"]]);
  });

  it("keeps a .mrxs with its data folder, including a .tif inside it", () => {
    const plan = planUploads(
      [sized("Case/S1.mrxs", big), item("Case/S1/Slidedat.ini"), item("Case/s1/Data0000.dat"), item("Case/S1/mask.tif"), sized("Case/x.svs", big)],
      false,
    );
    expect(paths(plan)).toEqual([["Case/S1.mrxs", "Case/S1/Slidedat.ini", "Case/s1/Data0000.dat", "Case/S1/mask.tif"], ["Case/x.svs"]]);
  });

  it("keeps a .vms with its tile files", () => {
    const plan = planUploads([item("V/scan.vms"), item("V/scan(0,0).jpg"), item("V/scan.opt"), item("V/other.jpg")], false);
    expect(paths(plan)).toEqual([["V/scan.vms", "V/scan(0,0).jpg", "V/scan.opt"]]);
    expect(plan.ignored).toBe(1);
  });

  it("never splits one slide across requests, however many files it has", () => {
    const data = Array.from({ length: 30 }, (_, i) => item(`S/Data${i}.dat`));
    const plan = planUploads([item("x.svs"), item("S.mrxs"), item("S/Slidedat.ini"), ...data], false, { maxBatchFiles: 10 });
    expect(plan.batches.map((b) => b.items.length)).toEqual([32, 1]);
  });

  it("does not send files that can't be slides, or system junk", () => {
    const plan = planUploads([item("F/a.svs"), item("F/notes.txt"), item("F/.DS_Store"), item("__MACOSX/F/._a.svs")], false);
    expect(paths(plan)).toEqual([["F/a.svs"]]);
    expect(plan.ignored).toBe(1);
  });

  it("image projects send images and zips, one unit each", () => {
    const plan = planUploads([item("P/a.png"), item("P/sub/b.JPG"), item("P/c.svs"), item("P/d.zip")], true, { maxBatchFiles: 2 });
    expect(paths(plan)).toEqual([["P/a.png", "P/sub/b.JPG"], ["P/d.zip"]]);
    expect(plan.ignored).toBe(1);
  });
});
