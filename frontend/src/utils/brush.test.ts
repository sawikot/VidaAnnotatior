import { describe, expect, it } from "vitest";
import type { Point } from "./coordinates";
import { polygonArea } from "./geometry";
import {
  addToShape,
  addToShapes,
  brushedType,
  containsPoint,
  eraseFromShape,
  paintShape,
  shapePaint,
  touchesShape,
  type BrushStroke,
  type BrushTarget,
} from "./brush";

const extent = { x0: 0, y0: 0, x1: 200, y1: 200 };
const stroke = (path: Point[], radius = 10, unit = 1): BrushStroke => ({ path, radius, unit, extent });
const square: BrushTarget = {
  type: "rectangle",
  points: [
    [50, 50],
    [150, 50],
    [150, 150],
    [50, 150],
  ],
};
/** Within a few percent: the round ends are many-sided polygons, not true arcs. */
const near = (actual: number, expected: number) => expect(Math.abs(actual - expected) / expected).toBeLessThan(0.03);

describe("paintShape", () => {
  it("turns a dab into a disc and a drag into a rounded bar", () => {
    near(polygonArea(paintShape(stroke([[100, 100]]))!), Math.PI * 100);
    near(polygonArea(paintShape(stroke([[50, 100], [150, 100]]))!), 100 * 20 + Math.PI * 100);
  });

  it("keeps the paint inside the extent", () => {
    const bar = paintShape(stroke([[-50, 5], [100, 5]]))!;
    expect(bar.every(([x, y]) => x >= 0 && y >= 0)).toBe(true);
    expect(paintShape(stroke([[-100, -100]]))).toBeNull();
  });

  it("uses fewer points when zoomed out", () => {
    const fine = paintShape(stroke([[100, 100]], 50, 0.25))!;
    const coarse = paintShape(stroke([[100, 100]], 50, 8))!;
    expect(coarse.length).toBeLessThan(fine.length);
  });
});

describe("addToShape", () => {
  it("grows the shape by the part of the stroke outside it", () => {
    const grown = addToShape(square, stroke([[100, 100], [180, 100]]))!;
    near(polygonArea(grown), 100 * 100 + 30 * 20 + (Math.PI * 100) / 2);
  });

  it("changes nothing when the stroke stays inside, or never comes near", () => {
    expect(addToShape(square, stroke([[80, 80], [120, 120]]))).toBeNull();
    expect(addToShape(square, stroke([[10, 10]], 5))).toBeNull();
  });

  it("keeps a shape of a neighbouring patch inside that patch", () => {
    const bounded = { ...square, bounds: { x0: 0, y0: 0, x1: 160, y1: 200 } };
    const grown = addToShape(bounded, stroke([[100, 100], [190, 100]]))!;
    expect(Math.max(...grown.map((p) => p[0]))).toBe(160);
  });

  it("takes a circle as the disc it is", () => {
    const circle: BrushTarget = { type: "circle", points: [[100, 100], [140, 100]] };
    const grown = addToShape(circle, stroke([[100, 100], [100, 30]]))!;
    expect(polygonArea(grown)).toBeGreaterThan(Math.PI * 40 * 40);
  });
});

describe("addToShapes", () => {
  const other: BrushTarget = { type: "circle", points: [[180, 100], [195, 100]] };

  it("knows which shapes the paint lies on", () => {
    expect(touchesShape(square, stroke([[20, 100], [45, 100]]))).toBe(true); // the edge of the brush reaches it
    expect(touchesShape(square, stroke([[20, 100], [30, 100]]))).toBe(false);
    expect(touchesShape(other, stroke([[100, 100]]))).toBe(false);
  });

  it("joins the shapes the stroke connects into one outline", () => {
    const joined = addToShapes([square, other], stroke([[140, 100], [175, 100]]))!;
    const xs = joined.map((p) => p[0]);
    expect(Math.min(...xs)).toBe(50);
    expect(Math.max(...xs)).toBeCloseTo(195, 0);
    expect(polygonArea(joined)).toBeGreaterThan(100 * 100 + Math.PI * 15 * 15);
  });

  it("grows a shape the stroke only starts outside of", () => {
    const grown = addToShapes([square], stroke([[20, 100], [60, 100]]))!;
    expect(Math.min(...grown.map((p) => p[0]))).toBeCloseTo(10, 0);
  });
});

describe("shapePaint", () => {
  const drawn = (x0: number, x1: number): Point[] => [
    [x0, 80],
    [x1, 80],
    [x1, 120],
    [x0, 120],
  ];

  it("joins a drawn rectangle onto the shape it overlaps, exactly", () => {
    const paint = shapePaint("rectangle", drawn(140, 190), 1, extent);
    expect(touchesShape(square, paint)).toBe(true);
    expect(polygonArea(addToShapes([square], paint)!)).toBe(100 * 100 + 40 * 40);
  });

  it("does not touch a shape it lies beside, and adds nothing drawn inside one", () => {
    expect(touchesShape(square, shapePaint("rectangle", drawn(160, 190), 1, extent))).toBe(false);
    expect(addToShapes([square], shapePaint("rectangle", drawn(60, 100), 1, extent))).toBeNull();
  });

  it("cuts a drawn rectangle out of a shape, splitting it when it goes right through", () => {
    const through: Point[] = [
      [100, 20],
      [120, 20],
      [120, 180],
      [100, 180],
    ];
    const pieces = eraseFromShape(square, shapePaint("rectangle", through, 1, extent))!;
    expect(pieces.map((p) => polygonArea(p))).toEqual([5000, 3000]);
    expect(eraseFromShape(square, shapePaint("rectangle", drawn(160, 190), 1, extent))).toBeNull();
  });

  it("takes a drawn circle as a disc", () => {
    const paint = shapePaint("circle", [[150, 100], [170, 100]], 1, extent);
    expect(polygonArea(addToShapes([square], paint)!)).toBeGreaterThan(100 * 100 + (Math.PI * 400) / 2 - 20);
  });
});

describe("eraseFromShape", () => {
  it("cuts a notch into the edge", () => {
    const pieces = eraseFromShape(square, stroke([[40, 100], [100, 100]]))!;
    expect(pieces).toHaveLength(1);
    near(polygonArea(pieces[0]), 100 * 100 - 50 * 20 - (Math.PI * 100) / 2);
  });

  it("splits the shape when the stroke goes right through, largest piece first", () => {
    const pieces = eraseFromShape(square, stroke([[120, 20], [120, 180]]))!;
    expect(pieces.map((p) => Math.round(polygonArea(p)))).toEqual([6000, 2000]);
  });

  it("leaves nothing when the stroke covers the shape", () => {
    expect(eraseFromShape(square, stroke([[100, 100]], 100))).toEqual([]);
  });

  it("leaves the shape alone when it is missed, and when only a hole would be left", () => {
    expect(eraseFromShape(square, stroke([[10, 10], [30, 10]]))).toBeNull();
    expect(eraseFromShape(square, stroke([[100, 100]]))).toBeNull();
  });

  it("does not touch points and lines", () => {
    expect(eraseFromShape({ type: "line", points: [[0, 0], [200, 200]] }, stroke([[100, 100]]))).toBeNull();
  });
});

describe("helpers", () => {
  it("knows what a point is on", () => {
    expect(containsPoint("rectangle", square.points, [100, 100])).toBe(true);
    expect(containsPoint("rectangle", square.points, [10, 100])).toBe(false);
    expect(containsPoint("circle", [[100, 100], [140, 100]], [130, 110])).toBe(true);
    expect(containsPoint("line", [[0, 0], [200, 200]], [100, 100])).toBe(false);
  });

  it("makes a reworked shape a free outline, except a polygon", () => {
    expect(brushedType("rectangle")).toBe("freehand");
    expect(brushedType("circle")).toBe("freehand");
    expect(brushedType("polygon")).toBe("polygon");
  });
});
