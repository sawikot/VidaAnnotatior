import { describe, expect, it } from "vitest";
import type { GeometryAnnotation, GeometryType } from "../types/api";
import { level0ToLocal, localToLevel0, localToLocal, projectSlideShapes, type PatchFrame } from "./slideProjection";

const frame = (over: Partial<PatchFrame> = {}): PatchFrame => ({ x: 1000, y: 2000, width: 512, height: 512, width_l0: 512, height_l0: 512, ...over });

function ann(id: number, type: GeometryType, coordinates_level0: [number, number][]): GeometryAnnotation {
  return {
    id, patch_id: null, slide_id: 1, config_version_id: 1, class_id: 3, type,
    coordinates_patch_local: [], coordinates_level0, created_by: null, notes: null,
    unsure: false, flagged: false, excluded: false, created_at: "", updated_at: "",
  }; // fmt: skip
}

describe("level0ToLocal", () => {
  it("is (level0 - origin) / downsample, the inverse of global = origin + local * downsample", () => {
    expect(level0ToLocal(frame(), [1100, 2080])).toEqual([100, 80]);
    const downsampled = frame({ width: 256, height: 256 }); // 512 Level-0 px shown as 256 px: downsample 2
    expect(level0ToLocal(downsampled, [1100, 2080])).toEqual([50, 40]);
  });

  it("round-trips the spec's worked example", () => {
    // origin (20000, 15000), local (100, 80) at 1:1 -> global (20100, 15080)
    expect(level0ToLocal(frame({ x: 20000, y: 15000 }), [20100, 15080])).toEqual([100, 80]);
  });
});

describe("projectSlideShapes", () => {
  it("moves a shape into the patch's pixels, keeping its type and class", () => {
    const [shape] = projectSlideShapes([ann(9, "rectangle", [[1100, 2100], [1200, 2100], [1200, 2200], [1100, 2200]])], frame());
    expect(shape).toMatchObject({ id: 9, type: "rectangle", class_id: 3 });
    expect(shape.points).toEqual([[100, 100], [200, 100], [200, 200], [100, 200]]);
  });

  it("keeps a circle a circle (centre and edge move together, scaled)", () => {
    const [shape] = projectSlideShapes([ann(1, "circle", [[1200, 2200], [1240, 2200]])], frame({ width: 256, height: 256 }));
    expect(shape.type).toBe("circle");
    expect(shape.points).toEqual([[100, 100], [120, 100]]); // radius 40 Level-0 px is 20 patch px at downsample 2
  });

  it("includes a shape that only partly overlaps (the patch clips it) and skips ones that cannot touch it", () => {
    const shapes = projectSlideShapes(
      [
        ann(1, "polygon", [[900, 2100], [1100, 2100], [1000, 2300]]), // sticks out to the left
        ann(2, "polygon", [[5000, 5000], [5100, 5000], [5050, 5100]]), // far away
        ann(3, "circle", [[1000, 1950], [1000, 1990]]), // r = 40 centred just above: its box reaches into the patch? no: bottom is 1990 < 2000
        ann(4, "point", [[1512, 2512]]), // exactly on the bottom-right corner
      ],
      frame(),
    );
    expect(shapes.map((s) => s.id)).toEqual([1, 4]);
  });

  it("uses the circle's radius, not just its two points, to decide whether it reaches the patch", () => {
    // centre well outside, but the radius (200) carries it into the patch
    const shapes = projectSlideShapes([ann(1, "circle", [[900, 2100], [1100, 2100]])], frame());
    expect(shapes.map((s) => s.id)).toEqual([1]);
  });

  it("ignores annotations with no coordinates", () => {
    expect(projectSlideShapes([ann(1, "polygon", [])], frame())).toEqual([]);
  });
});

describe("points between overlapping patches", () => {
  // 2048 px patches read at 4x (footprint 8192 L0 px), stride half a patch: the second starts 4096 L0 px to the right.
  const first = frame({ x: 0, y: 0, width: 2048, height: 2048, width_l0: 8192, height_l0: 8192 });
  const second = frame({ x: 4096, y: 0, width: 2048, height: 2048, width_l0: 8192, height_l0: 8192 });

  it("a patch pixel lands at origin + local * downsample on the slide", () => {
    expect(localToLevel0(second, [100, 50])).toEqual([4096 + 400, 200]);
  });

  it("the shared half of the first patch is the start of the second", () => {
    expect(localToLocal(first, second, [1024, 10])).toEqual([0, 10]);
    expect(localToLocal(first, second, [1500, 700])).toEqual([476, 700]);
  });

  it("converting there and back gives the same point", () => {
    const back = localToLocal(second, first, localToLocal(first, second, [1234.5, 99.25]));
    expect(back[0]).toBeCloseTo(1234.5);
    expect(back[1]).toBeCloseTo(99.25);
  });
});
