import { describe, expect, it } from "vitest";
import { pageItems } from "./pagination";

describe("pageItems", () => {
  it("shows every page when there are few", () => {
    expect(pageItems(0, 1)).toEqual([0]);
    expect(pageItems(3, 7)).toEqual([0, 1, 2, 3, 4, 5, 6]);
  });

  it("shows the first, the last and the neighbours of the current page, with gaps", () => {
    expect(pageItems(6, 178)).toEqual([0, "gap", 5, 6, 7, "gap", 177]);
  });

  it("keeps the same length near either end instead of a gap for one page", () => {
    expect(pageItems(0, 178)).toEqual([0, 1, 2, 3, 4, "gap", 177]);
    expect(pageItems(3, 178)).toEqual([0, 1, 2, 3, 4, "gap", 177]);
    expect(pageItems(177, 178)).toEqual([0, "gap", 173, 174, 175, 176, 177]);
    expect(pageItems(174, 178)).toEqual([0, "gap", 173, 174, 175, 176, 177]);
  });

  it("clamps a page past either end", () => {
    expect(pageItems(500, 178)).toEqual(pageItems(177, 178));
    expect(pageItems(-4, 178)).toEqual(pageItems(0, 178));
  });

  it("every row has 7 slots once there are more than 7 pages", () => {
    for (let p = 0; p < 50; p++) expect(pageItems(p, 50)).toHaveLength(7);
  });
});
