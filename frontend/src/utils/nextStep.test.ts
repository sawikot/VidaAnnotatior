import { describe, expect, it } from "vitest";
import type { ProjectDetail, Slide } from "../types/api";
import { projectChecklist, slideNextStep } from "./nextStep";

const slide = (over: Partial<Slide>): Slide => ({ id: 1, status: "imported", patch_count: 0, annotated_patch_count: 0, reviewed_patch_count: 0, ...over }) as Slide;
const project = (stats: Partial<ProjectDetail["stats"]>, type: "wsi" | "image" = "wsi"): ProjectDetail =>
  ({
    project_type: type,
    stats: { slide_count: 0, processed_slide_count: 0, total_patches: 0, annotated_patches: 0, reviewed_patches: 0, flagged_patches: 0, tissue_area_mm2: 0, ...stats },
  }) as ProjectDetail;

describe("what a slide needs next", () => {
  it("walks a slide from import to export", () => {
    expect(slideNextStep(slide({ status: "imported" }))).toMatchObject({ label: "Find tissue", target: "processing" });
    expect(slideNextStep(slide({ status: "tissue_detected" }))).toMatchObject({ label: "Generate patches", target: "processing" });
    expect(slideNextStep(slide({ status: "patches_generated", patch_count: 80 }))).toMatchObject({ label: "Start annotating", target: "workspace" });
    const going = slideNextStep(slide({ status: "annotating", patch_count: 80, annotated_patch_count: 12, reviewed_patch_count: 3 }));
    expect(going).toMatchObject({ label: "Continue", target: "workspace" });
    expect(going.detail).toBe("12 of 80 patches annotated, 3 reviewed.");
    expect(slideNextStep(slide({ status: "reviewed", patch_count: 80, annotated_patch_count: 12, reviewed_patch_count: 12 }))).toMatchObject({
      label: "Export",
      target: "export",
      tone: "done",
    });
  });

  it("points out a failed import", () => {
    expect(slideNextStep(slide({ status: "error", error_message: "unreadable" }))).toMatchObject({ tone: "problem", target: null, detail: "unreadable" });
  });
});

describe("project checklist", () => {
  it("starts at adding slides", () => {
    const steps = projectChecklist(project({}), []);
    expect(steps.map((s) => s.id)).toEqual(["add", "tissue", "patches", "annotate", "review", "export"]);
    expect(steps.find((s) => s.current)?.id).toBe("add");
  });

  it("the current step is the first unfinished one, and opens the slide that needs it", () => {
    const slides = [slide({ id: 1, status: "patches_generated", patch_count: 10 }), slide({ id: 2, status: "imported" })];
    const steps = projectChecklist(project({ total_patches: 10 }), slides);
    const tissue = steps.find((s) => s.id === "tissue")!;
    expect(tissue).toMatchObject({ current: true, done: false, slideId: 2, progress: "1 of 2 slides" });
  });

  it("an image project skips tissue and patches", () => {
    const steps = projectChecklist(project({ total_patches: 5, annotated_patches: 5, reviewed_patches: 2 }, "image"), [
      slide({ id: 1, status: "patches_generated", patch_count: 1, annotated_patch_count: 1 }),
    ]);
    expect(steps.map((s) => s.id)).toEqual(["add", "annotate", "review", "export"]);
    expect(steps.find((s) => s.current)?.id).toBe("review");
  });
});
