import { describe, expect, it } from "vitest";
import type { ImageSummary } from "../../types/api";
import { firstToAnnotate, neighbour, progress } from "./imageNav";

function img(slide_id: number, over: Partial<ImageSummary> = {}): ImageSummary {
  return {
    slide_id,
    patch_id: slide_id * 10,
    filename: `${slide_id}.png`,
    width: 64,
    height: 48,
    status: "unannotated",
    unsure: false,
    flagged: false,
    excluded: false,
    annotation_count: 0,
    ...over,
  };
}

const LIST = [
  img(1, { status: "annotated" }),
  img(2),
  img(3, { status: "reviewed", flagged: true }),
  img(4),
  img(5, { status: "skipped", flagged: true }),
];

describe("neighbour", () => {
  it("moves one image in either direction", () => {
    expect(neighbour(LIST, 2, "next")?.slide_id).toBe(3);
    expect(neighbour(LIST, 2, "prev")?.slide_id).toBe(1);
  });

  it("returns null at the ends instead of wrapping", () => {
    expect(neighbour(LIST, 5, "next")).toBeNull();
    expect(neighbour(LIST, 1, "prev")).toBeNull();
  });

  it("skips images that don't match the filter", () => {
    expect(neighbour(LIST, 2, "next", "unannotated")?.slide_id).toBe(4);
    expect(neighbour(LIST, 4, "next", "unannotated")).toBeNull();
    expect(neighbour(LIST, 1, "next", "flagged")?.slide_id).toBe(3);
    expect(neighbour(LIST, 5, "prev", "flagged")?.slide_id).toBe(3);
  });

  it("never returns the current image", () => {
    expect(neighbour(LIST, 3, "next", "flagged")?.slide_id).toBe(5);
    expect(neighbour(LIST, 4, "prev", "unannotated")?.slide_id).toBe(2);
  });

  it("starts from the ends when the current image isn't in the list", () => {
    expect(neighbour(LIST, 99, "next")?.slide_id).toBe(1);
    expect(neighbour(LIST, 99, "prev")?.slide_id).toBe(5);
  });

  it("handles an empty list", () => {
    expect(neighbour([], 1, "next")).toBeNull();
  });
});

describe("firstToAnnotate / progress", () => {
  it("prefers the first unannotated image, falls back to the first image", () => {
    expect(firstToAnnotate(LIST)?.slide_id).toBe(2);
    expect(firstToAnnotate([img(7, { status: "reviewed" }), img(8, { status: "annotated" })])?.slide_id).toBe(7);
    expect(firstToAnnotate([])).toBeNull();
  });

  it("counts annotated and reviewed images as done", () => {
    expect(progress(LIST)).toEqual({ total: 5, done: 2 });
    expect(progress([])).toEqual({ total: 0, done: 0 });
  });
});
