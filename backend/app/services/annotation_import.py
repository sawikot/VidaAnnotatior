"""Reading annotation files made elsewhere into import entries.

Every reader turns its format into the one shape ``POST /slides/{id}/import-annotations`` takes --
``{type, label, coordinates (Level-0), source_patch, unsure, flagged}`` -- so matching labels to
classes, placing shapes in patches and skipping duplicates happen in one place whatever the source.

Formats (detected from the content, not the file name):

* ``wsi_json``   this app's own export (or a bare ``annotations`` array)
* ``geojson``    QuPath and GIS tools, and this app's GeoJSON export (Level-0 pixels)
* ``coco``       COCO instance segmentation: this app's export (exact Level-0 via ``vp_*`` fields)
                 or any other, whose image-relative pixels are placed by the image's position
* ``asap_xml``   ASAP
* ``aperio_xml`` Aperio ImageScope
* ``csv``        one row per shape: a point (x, y), a box, or a WKT geometry, with a label column
* ``cytomine``   Cytomine annotation lists (WKT ``location``, y counted from the image's bottom, and
                 ``term`` ids matched to the classes' own Class IDs)

Nearly every format stores full-resolution (Level-0) pixels, so the scale is 1. A file drawn on a
smaller copy of the slide (a thumbnail, a lower level) needs its coordinates multiplied up: with no
``scale`` given it is found from the file where the file shows it (a COCO file whose one image is
the whole slide at a smaller size); a given ``scale`` is used as it is.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterator

from shapely import wkt as shapely_wkt
from shapely.errors import ShapelyError
from shapely.geometry import MultiPolygon, Polygon, mapping
from shapely.ops import transform as shapely_transform
from shapely.ops import unary_union

from app.services.geometry import SHAPE_TYPES, circle_center_radius, clean_ring, polygon_bounds, validate_shape

FORMAT_NAMES = {
    "wsi_json": "WSI JSON (this app)",
    "geojson": "GeoJSON",
    "coco": "COCO JSON",
    "asap_xml": "ASAP XML",
    "aperio_xml": "Aperio ImageScope XML",
    "csv": "CSV / TSV table",
    "cytomine": "Cytomine JSON (term IDs)",
}

SUPPORTED = "WSI JSON, GeoJSON (QuPath), COCO JSON, Cytomine JSON, ASAP XML, Aperio ImageScope XML, or CSV/TSV"


class ImportFormatError(ValueError):
    """The file can't be read as any supported format (the message is shown to the user)."""


@dataclass
class ParsedFile:
    format: str
    scale: float = 1.0
    auto_scale: bool = False  # the scale may be worked out from the file
    scale_note: str | None = None  # why the scale is not 1, when it was worked out
    entries: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unreadable: Counter = field(default_factory=Counter)  # reason -> shapes in the file that could not be used

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def add(
        self,
        shape_type: str,
        coords: list[list[float]],
        label: Any = None,
        *,
        source_patch: dict | None = None,
        unsure: Any = False,
        flagged: Any = False,
    ) -> None:
        """One shape, ``coords`` in the file's slide pixels (``scale`` is applied here)."""
        try:
            points = [[float(p[0]) * self.scale, float(p[1]) * self.scale] for p in coords]
        except (TypeError, ValueError, IndexError):
            self.unreadable["malformed coordinates"] += 1
            return
        if shape_type in ("polygon", "freehand"):
            points = [list(p) for p in clean_ring(points)]
        try:
            validate_shape(shape_type, points)
        except ValueError:
            self.unreadable["malformed shape"] += 1
            return
        if self.scale != 1.0:
            source_patch = None  # the file's patch origins are in its own (unscaled) space
        self.entries.append(
            {
                "type": shape_type,
                "label": _label_text(label),
                "coordinates": points,
                "source_patch": source_patch,
                "unsure": _truthy(unsure),
                "flagged": _truthy(flagged),
            }
        )


def _label_text(label: Any) -> str | None:
    if label is None:
        return None
    text = str(label).strip()
    return text or None


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "t")
    return bool(value)


def _num(value: Any) -> float:
    """A coordinate from text; ASAP writes a decimal comma under some locales."""
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("not a finite number")
    return number


# --------------------------------------------------------------------------- shape helpers


def _open_ring(ring: list) -> list[list[float]]:
    """Cleaned outline without the repeated closing vertex (any z values dropped)."""
    return [[x, y] for x, y in clean_ring([[p[0], p[1]] for p in ring])]


def _is_axis_aligned_box(ring: list[list[float]]) -> bool:
    if len(ring) != 4:
        return False
    xs = {round(p[0], 3) for p in ring}
    ys = {round(p[1], 3) for p in ring}
    return len(xs) == 2 and len(ys) == 2


def _circle_from_ring(ring: list[list[float]]) -> list[list[float]] | None:
    """A regular outline (like the 64-gon a circle is exported as) back to centre + edge point."""
    if len(ring) < 16:
        return None
    cx = sum(p[0] for p in ring) / len(ring)
    cy = sum(p[1] for p in ring) / len(ring)
    distances = [math.hypot(p[0] - cx, p[1] - cy) for p in ring]
    r = sum(distances) / len(distances)
    if r <= 0 or max(abs(d - r) for d in distances) > 0.02 * r:
        return None
    return [[cx, cy], [cx + r, cy]]


def _area_shape(ring: list[list[float]], hint: str | None = None) -> tuple[str, list[list[float]]]:
    """An outline as the app's best-fitting area type: a restored circle or rectangle, else a polygon."""
    if hint == "circle":
        circle = _circle_from_ring(ring)
        if circle:
            return "circle", circle
    if hint in (None, "rectangle", "polygon") and _is_axis_aligned_box(ring):
        return "rectangle", ring
    return ("freehand" if hint == "freehand" else "polygon"), ring


def _ellipse_ring(x0: float, y0: float, x1: float, y1: float, segments: int = 64) -> list[list[float]]:
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, abs(x1 - x0) / 2, abs(y1 - y0) / 2
    return [[cx + rx * math.cos(2 * math.pi * i / segments), cy + ry * math.sin(2 * math.pi * i / segments)] for i in range(segments)]


def _box(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def shape_bounds(shape_type: str, coords: list[list[float]]) -> tuple[float, float, float, float]:
    """(min_x, min_y, max_x, max_y) of the shape itself (a circle reaches its radius round the centre)."""
    if shape_type == "circle":
        (cx, cy), r = circle_center_radius(coords)
        return cx - r, cy - r, cx + r, cy + r
    return polygon_bounds(coords)


# --------------------------------------------------------------------------- GeoJSON

GEOMETRY_TYPES = {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon", "GeometryCollection"}


def _geojson_shapes(geom: dict, hint: str | None, out: ParsedFile) -> Iterator[tuple[str, list[list[float]]]]:
    kind = geom.get("type")
    coords = geom.get("coordinates")
    if kind == "Point":
        yield "point", [coords]
    elif kind == "MultiPoint":
        for point in coords or []:
            yield "point", [point]
    elif kind == "LineString":
        line = [[p[0], p[1]] for p in coords or []]
        yield ("line" if len(line) == 2 and hint != "freehand_line" else "freehand_line"), line
    elif kind == "MultiLineString":
        for part in coords or []:
            yield from _geojson_shapes({"type": "LineString", "coordinates": part}, hint, out)
    elif kind == "Polygon":
        rings = coords or []
        if not rings:
            return
        if len(rings) > 1:
            out.warn("Polygons with holes were imported as their outer outline (the app does not store holes).")
        yield _area_shape(_open_ring(rings[0]), hint)
    elif kind == "MultiPolygon":
        for part in coords or []:
            yield from _geojson_shapes({"type": "Polygon", "coordinates": part}, hint, out)
    elif kind == "GeometryCollection":
        for part in geom.get("geometries") or []:
            yield from _geojson_shapes(part, hint, out)
    else:
        out.unreadable[f"unsupported geometry '{kind}'"] += 1


def _geojson_label(props: dict) -> Any:
    classification = props.get("classification")
    if isinstance(classification, dict) and classification.get("name"):
        return classification["name"]
    if isinstance(classification, str) and classification:
        return classification
    for key in ("label", "class", "class_name", "classname", "category", "name"):
        value = props.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return value
    return None


def _parse_geojson(doc: Any, out: ParsedFile) -> None:
    if isinstance(doc, list):
        items = doc
    elif doc.get("type") == "FeatureCollection" or "features" in doc:
        items = doc.get("features") or []
    else:
        items = [doc]

    for item in items:
        if not isinstance(item, dict):
            out.unreadable["not a feature"] += 1
            continue
        if item.get("type") == "Feature":
            geom, props = item.get("geometry"), item.get("properties") or {}
        else:
            geom, props = item, {}
        if not isinstance(geom, dict):
            out.unreadable["feature without geometry"] += 1
            continue

        hint = props.get("shape_type") if props.get("shape_type") in SHAPE_TYPES else None
        source_patch = None
        # This app's own GeoJSON export names the patch a shape was drawn in.
        if props.get("drawn_in") == "patch" and props.get("patch_x") is not None and props.get("patch_y") is not None:
            source_patch = {"x": props["patch_x"], "y": props["patch_y"]}
        for shape_type, coords in _geojson_shapes(geom, hint, out):
            out.add(
                shape_type,
                coords,
                _geojson_label(props),
                source_patch=source_patch,
                unsure=props.get("unsure", False),
                flagged=props.get("flagged", False),
            )


# --------------------------------------------------------------------------- COCO

_TILE_NAME = re.compile(r"[_-]x(\d+)[_-]y(\d+)", re.IGNORECASE)


def _flat_to_ring(flat: list) -> list[list[float]]:
    return [[flat[i], flat[i + 1]] for i in range(0, len(flat) - 1, 2)]


def _coco_rings(ann: dict, out: ParsedFile) -> list[list[list[float]]]:
    """The annotation's outlines in its image's pixels (a box when there is no polygon)."""
    seg = ann.get("segmentation")
    if isinstance(seg, list) and seg and isinstance(seg[0], list):
        return [_flat_to_ring(part) for part in seg if len(part) >= 6]
    if isinstance(seg, list) and len(seg) >= 6 and all(isinstance(v, (int, float)) for v in seg):
        return [_flat_to_ring(seg)]
    bbox = ann.get("bbox")
    if isinstance(bbox, list) and len(bbox) == 4:
        if isinstance(seg, dict):
            out.warn("COCO RLE masks were imported as their bounding boxes.")
        x, y, w, h = (float(v) for v in bbox)
        return [_box(x, y, x + w, y + h)]
    return []


def _detect_coco_scale(images: dict, out: ParsedFile, slide_size: tuple[int | None, int | None]) -> None:
    """One image showing the whole slide at a smaller size: scale its pixels up to Level-0."""
    width, height = slide_size
    if not out.auto_scale or len(images) != 1 or not width or not height:
        return
    (img,) = images.values()
    try:
        w, h = float(img.get("width") or 0), float(img.get("height") or 0)
    except (TypeError, ValueError):
        return
    if w <= 0 or h <= 0:
        return
    rx, ry = width / w, height / h
    if rx > 1.05 and abs(rx - ry) / rx < 0.02:  # smaller, with the slide's proportions
        out.scale = round((rx + ry) / 2, 4)
        out.scale_note = (
            f"The COCO image is {w:g} × {h:g} px, the whole slide {out.scale:g}× smaller, "
            "so its coordinates were scaled up to full resolution."
        )


def _parse_coco(doc: dict, out: ParsedFile, slide_size: tuple[int | None, int | None] = (None, None)) -> None:
    categories = {c.get("id"): c.get("name") for c in doc.get("categories") or [] if isinstance(c, dict)}
    images = {img.get("id"): img for img in doc.get("images") or [] if isinstance(img, dict)}

    # image id -> (Level-0 origin, downsample, is a patch of this app)
    placement: dict[Any, tuple[float, float, float, bool]] = {}
    unplaced = 0
    for image_id, img in images.items():
        origin = img.get("vp_origin_level0")
        if isinstance(origin, list) and len(origin) == 2:
            placement[image_id] = (float(origin[0]), float(origin[1]), float(img.get("vp_downsample") or 1.0), True)
            continue
        match = _TILE_NAME.search(str(img.get("file_name") or ""))
        if match:
            placement[image_id] = (float(match.group(1)), float(match.group(2)), 1.0, False)
        else:
            placement[image_id] = (0.0, 0.0, 1.0, False)
            unplaced += 1
    if unplaced == len(images):
        _detect_coco_scale(images, out, slide_size)
    if unplaced > 1:
        out.warn(
            f"{unplaced} COCO images carry no position on the slide, so their coordinates were taken as "
            "slide pixels. Use COCO made from the whole slide, or tile names containing _x<left>_y<top>."
        )

    pieces: dict[Any, list[dict]] = defaultdict(list)  # this app's slide-level shapes, cut into patches
    for ann in doc.get("annotations") or []:
        if not isinstance(ann, dict):
            continue
        label = categories.get(ann.get("category_id"))
        hint = ann.get("vp_shape_type") if ann.get("vp_shape_type") in SHAPE_TYPES else None
        flags = {"unsure": ann.get("vp_unsure", False), "flagged": ann.get("vp_flagged", False)}

        if ann.get("vp_scope") is not None and ann.get("vp_level0_segmentation"):
            # Exported through the patches it covers: put the whole shape back together below.
            pieces[ann.get("vp_source_annotation_id")].append(ann)
            continue

        if ann.get("vp_level0_segmentation"):
            ox, oy, _ds, own = placement.get(ann.get("image_id"), (0.0, 0.0, 1.0, False))
            source_patch = {"x": int(ox), "y": int(oy)} if own else None
            for flat in ann["vp_level0_segmentation"]:
                shape_type, ring = _area_shape(_open_ring(_flat_to_ring(flat)), hint)
                out.add(shape_type, ring, label, source_patch=source_patch, **flags)
            continue

        ox, oy, ds, _own = placement.get(ann.get("image_id"), (0.0, 0.0, 1.0, False))
        for ring in _coco_rings(ann, out):
            level0 = [[ox + float(x) * ds, oy + float(y) * ds] for x, y in ring]
            shape_type, level0 = _area_shape(_open_ring(level0), hint)
            out.add(shape_type, level0, label, **flags)

    for group in pieces.values():
        first = group[0]
        label = categories.get(first.get("category_id"))
        hint = first.get("vp_shape_type") if first.get("vp_shape_type") in SHAPE_TYPES else None
        flags = {"unsure": first.get("vp_unsure", False), "flagged": first.get("vp_flagged", False)}
        whole = next((p for p in group if not p.get("vp_clipped")), None)
        if whole is not None:
            rings = [_open_ring(_flat_to_ring(flat)) for flat in whole["vp_level0_segmentation"]]
        else:
            polygons = []
            for piece in group:
                for flat in piece["vp_level0_segmentation"]:
                    ring = _open_ring(_flat_to_ring(flat))
                    if len(ring) >= 3:
                        polygons.append(Polygon(ring).buffer(0))
            merged = unary_union(polygons) if polygons else None
            parts = list(merged.geoms) if isinstance(merged, MultiPolygon) else [merged] if isinstance(merged, Polygon) else []
            rings = [_open_ring(list(part.exterior.coords)) for part in parts if not part.is_empty]
            hint = None if hint == "circle" else hint
        for ring in rings:
            shape_type, ring = _area_shape(ring, hint)
            out.add(shape_type, ring, label, **flags)


# --------------------------------------------------------------------------- WSI JSON


def _parse_wsi_json(items: list, out: ParsedFile) -> None:
    for item in items:
        if not isinstance(item, dict) or item.get("type") not in SHAPE_TYPES or not isinstance(item.get("coordinates"), list):
            out.unreadable["malformed shape"] += 1
            continue
        patch = item.get("source_patch")
        source_patch = {"x": patch.get("x"), "y": patch.get("y")} if isinstance(patch, dict) else None
        out.add(
            item["type"],
            item["coordinates"],
            item.get("label"),
            source_patch=source_patch,
            unsure=item.get("unsure", False),
            flagged=item.get("flagged", False),
        )


# --------------------------------------------------------------------------- Cytomine


def _parse_cytomine(items: list, out: ParsedFile, height: int | None, class_codes: list[tuple[int, str]]) -> None:
    """Cytomine annotations: a WKT ``location`` whose y counts up from the image's *bottom* edge, and
    ``term``, a list of term ids. A term id is matched to a class with that Class ID; when several
    match, the class listed first in the configuration wins. A shape with no matching term gets a
    ``Term <ids>`` (or ``No term``) label, which is skipped unless mapped to a class."""
    if not height:
        raise ImportFormatError("Cytomine counts y from the bottom of the image, so the slide's height is needed; it is unknown for this slide.")
    rank = {code: (position, name) for position, (code, name) in enumerate(class_codes)}
    several = 0
    unmatched: Counter = Counter()

    def flip(x, y, z=None):
        return x, height - y

    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("location"), str):
            out.unreadable["annotation without a location"] += 1
            continue
        try:
            geom = shapely_transform(flip, shapely_wkt.loads(item["location"]))
        except (ShapelyError, ValueError, TypeError):
            out.unreadable["unreadable WKT location"] += 1
            continue
        terms = [t for t in item.get("term") or [] if isinstance(t, int)]
        matches = sorted(rank[t] for t in terms if t in rank)
        if matches:
            label = matches[0][1]
            several += len({m[1] for m in matches}) > 1
        else:
            label = f"Term {', '.join(map(str, terms))}" if terms else "No term"
            unmatched.update(terms or ["none"])
        for shape_type, coords in _geojson_shapes(mapping(geom), None, out):
            out.add(shape_type, coords, label)

    if several:
        out.warn(f"{several} annotation(s) have terms of several classes; each was given the class listed first in the configuration.")
    if unmatched:
        ids = ", ".join(f"{t} ({n})" for t, n in unmatched.most_common() if t != "none")
        out.warn(
            ("Term IDs with no class of that Class ID: " + ids + ". " if ids else "")
            + "Shapes with no matching term are skipped unless you pick a class for them below."
        )


# --------------------------------------------------------------------------- XML (ASAP, Aperio)


def _parse_asap(root: ET.Element, out: ParsedFile) -> None:
    for ann in root.iter("Annotation"):
        group = ann.get("PartOfGroup")
        label = group if group and group.lower() != "none" else None
        try:
            coords = sorted(
                ((int(c.get("Order") or i), [_num(c.get("X")), _num(c.get("Y"))]) for i, c in enumerate(ann.iter("Coordinate"))),
                key=lambda item: item[0],
            )
        except (TypeError, ValueError):
            out.unreadable["malformed coordinates"] += 1
            continue
        points = [p for _, p in coords]
        kind = (ann.get("Type") or "").lower()
        if kind == "pointset" or (kind == "dot" and len(points) > 1):
            for point in points:
                out.add("point", [point], label)
        elif kind == "dot" or len(points) == 1:
            out.add("point", points[:1], label)
        elif kind == "rectangle" and len(points) == 4:
            out.add("rectangle", points, label)
        elif len(points) == 2:
            out.add("line", points, label)
        else:
            out.add("polygon", points, label)


def _parse_aperio(root: ET.Element, out: ParsedFile) -> None:
    for ann in root.iter("Annotation"):
        label = ann.get("Name") or None
        if not label:
            attribute = ann.find("./Attributes/Attribute")
            label = attribute.get("Name") or attribute.get("Value") if attribute is not None else None
        for region in ann.iter("Region"):
            if region.get("NegativeROA") == "1":
                out.warn("Aperio negative regions (holes) were left out.")
                out.unreadable["negative region"] += 1
                continue
            try:
                points = [[_num(v.get("X")), _num(v.get("Y"))] for v in region.iter("Vertex")]
            except (TypeError, ValueError):
                out.unreadable["malformed coordinates"] += 1
                continue
            region_label = label or region.get("Text") or None
            kind = region.get("Type")
            if kind == "2" and len(points) >= 2:  # ellipse: two opposite corners of its box
                (x0, y0), (x1, y1) = points[0], points[1]
                if abs(abs(x1 - x0) - abs(y1 - y0)) <= 0.01 * max(abs(x1 - x0), abs(y1 - y0), 1):
                    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                    out.add("circle", [[cx, cy], [cx + abs(x1 - x0) / 2, cy]], region_label)
                else:
                    out.add("polygon", _ellipse_ring(x0, y0, x1, y1), region_label)
            elif kind == "1" and len(points) == 2:
                out.add("rectangle", _box(points[0][0], points[0][1], points[1][0], points[1][1]), region_label)
            elif kind == "5" or len(points) == 1:
                out.add("point", points[:1], region_label)
            elif kind in ("3", "4") or len(points) == 2:  # arrow, ruler
                out.add("line", points[:2], region_label)
            else:
                shape_type, ring = _area_shape(_open_ring(points))
                out.add(shape_type, ring, region_label)


def _parse_xml(text: str, out: ParsedFile) -> None:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise ImportFormatError(f"The file looks like XML but can't be read: {e}") from None
    if root.tag == "ASAP_Annotations":
        out.format = "asap_xml"
        _parse_asap(root, out)
    elif root.tag == "Annotations" and root.find(".//Region") is not None:
        out.format = "aperio_xml"
        _parse_aperio(root, out)
    elif root.tag == "Annotations":
        out.format = "aperio_xml"  # an Aperio file with no regions: nothing to import
    else:
        raise ImportFormatError(f"Unrecognised XML (root <{root.tag}>). Supported: {SUPPORTED}.")


# --------------------------------------------------------------------------- CSV / TSV


def _key(header: str) -> str:
    return re.sub(r"[^a-z0-9µ]", "", header.lower().replace("μ", "µ"))


X_NAMES = ["x", "cx", "xpx", "centroidx", "centerx", "centrex", "xcoord", "xcoordinate", "xposition", "posx", "positionx", "locationx"]
Y_NAMES = [n.replace("x", "y") for n in X_NAMES]
LABEL_NAMES = ["label", "class", "classname", "classification", "category", "categoryname", "annotationclass", "name"]
WKT_NAMES = ["wkt", "geometry", "geom", "roi", "shape"]


def _parse_table(text: str, filename: str, out: ParsedFile, mpp: tuple[float | None, float | None]) -> None:
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel_tab if filename.lower().endswith(".tsv") else csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    columns = {_key(h): h for h in reader.fieldnames or [] if h}

    def col(names: list[str], suffixes: tuple[str, ...] = ("",)) -> str | None:
        for name in names:
            for suffix in suffixes:
                if name + suffix in columns:
                    return columns[name + suffix]
        return None

    label_col = col(LABEL_NAMES)
    unsure_col, flagged_col = col(["unsure"]), col(["flagged"])
    wkt_col = col(WKT_NAMES)
    box_cols = [col(["xmin", "x1", "left", "bboxx1", "bboxxmin"]), col(["ymin", "y1", "top", "bboxy1", "bboxymin"]),
                col(["xmax", "x2", "right", "bboxx2", "bboxxmax"]), col(["ymax", "y2", "bottom", "bboxy2", "bboxymax"])]
    x_col, y_col = col(X_NAMES), col(Y_NAMES)
    size_cols = [col(["width", "w"]), col(["height", "h"])]
    in_microns = False
    if not (x_col and y_col) and not wkt_col and not all(box_cols):
        x_col, y_col = col(X_NAMES, ("µm", "um", "micron", "microns")), col(Y_NAMES, ("µm", "um", "micron", "microns"))
        if x_col and y_col:
            if not mpp[0] or not mpp[1]:
                raise ImportFormatError("The table's coordinates are in µm, but this slide has no pixel size (mpp) to convert them.")
            if abs(mpp[0] - mpp[1]) > 1e-6:
                out.warn("The slide's pixels are not square; µm were converted with each axis's own pixel size.")
            in_microns = True
            out.warn(f"Coordinates were converted from µm with the slide's pixel size ({mpp[0]:g} µm/px).")

    if not wkt_col and not all(box_cols) and not (x_col and y_col):
        raise ImportFormatError(
            "The table needs x and y columns (points), xmin/ymin/xmax/ymax (boxes), x/y/width/height, "
            "or a WKT geometry column. Found: " + ", ".join(reader.fieldnames or [])
        )

    for row in reader:
        label = row.get(label_col) if label_col else None
        flags = {"unsure": row.get(unsure_col, False) if unsure_col else False,
                 "flagged": row.get(flagged_col, False) if flagged_col else False}
        try:
            if wkt_col and (row.get(wkt_col) or "").strip():
                geom = shapely_wkt.loads(row[wkt_col])
                for shape_type, coords in _geojson_shapes(mapping(geom), None, out):
                    out.add(shape_type, coords, label, **flags)
            elif all(box_cols):
                x0, y0, x1, y1 = (_num(row[c]) for c in box_cols)
                out.add("rectangle", _box(x0, y0, x1, y1), label, **flags)
            elif x_col and y_col and all(size_cols) and (row.get(size_cols[0]) or "").strip():
                x, y, w, h = _num(row[x_col]), _num(row[y_col]), _num(row[size_cols[0]]), _num(row[size_cols[1]])
                out.add("rectangle", _box(x, y, x + w, y + h), label, **flags)
            elif x_col and y_col:
                x, y = _num(row[x_col]), _num(row[y_col])
                if in_microns:
                    x, y = x / mpp[0], y / mpp[1]
                out.add("point", [[x, y]], label, **flags)
            else:
                out.unreadable["row without a shape"] += 1
        except (TypeError, ValueError, KeyError, ShapelyError):
            out.unreadable["unreadable row"] += 1


# --------------------------------------------------------------------------- entry point


def _json_format(doc: Any) -> str:
    if isinstance(doc, dict):
        if isinstance(doc.get("collection"), list):  # a Cytomine API page: {"collection": [...], ...}
            return _json_format(doc["collection"])
        if doc.get("type") in ("FeatureCollection", "Feature") or doc.get("type") in GEOMETRY_TYPES or "features" in doc:
            return "geojson"
        if isinstance(doc.get("annotations"), list) and ("categories" in doc or "images" in doc):
            return "coco"
        if isinstance(doc.get("annotations"), list):
            return "wsi_json"
    elif isinstance(doc, list):
        first = next((item for item in doc if isinstance(item, dict)), None)
        if first is None:
            return "wsi_json"
        if isinstance(first.get("location"), str) and "term" in first:
            return "cytomine"
        if first.get("type") == "Feature" or first.get("type") in GEOMETRY_TYPES:
            return "geojson"
        if "coordinates" in first:
            return "wsi_json"
    raise ImportFormatError(f"Unrecognised JSON layout. Supported: {SUPPORTED}.")


def parse_annotation_file(
    filename: str,
    raw: bytes,
    *,
    scale: float | None = None,
    mpp: tuple[float | None, float | None] = (None, None),
    slide_size: tuple[int | None, int | None] = (None, None),
    class_codes: list[tuple[int, str]] | None = None,
) -> ParsedFile:
    """Reads an annotation file of any supported format into import entries (Level-0 coordinates).

    ``class_codes`` is the configuration's (Class ID, name) pairs in class order, which formats that
    identify classes by number (Cytomine terms) are matched against. ``scale=None`` works the
    scale out from the file (1 unless the file shows otherwise)."""
    if scale is not None and (not math.isfinite(scale) or scale <= 0):
        raise ImportFormatError("The scale must be a positive number.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    body = text.lstrip()
    if not body:
        raise ImportFormatError("The file is empty.")

    out = ParsedFile(format="", scale=scale or 1.0, auto_scale=scale is None)
    if body[0] in "{[":
        try:
            doc = json.loads(body)
        except json.JSONDecodeError as e:
            raise ImportFormatError(f"The file looks like JSON but can't be read: {e}") from None
        out.format = _json_format(doc)
        if out.format == "geojson":
            _parse_geojson(doc, out)
        elif out.format == "coco":
            _parse_coco(doc, out, slide_size)
        elif out.format == "cytomine":
            items = doc["collection"] if isinstance(doc, dict) else doc
            _parse_cytomine(items, out, slide_size[1], class_codes or [])
        else:
            _parse_wsi_json(doc if isinstance(doc, list) else doc["annotations"], out)
    elif body[0] == "<":
        _parse_xml(body, out)
    else:
        out.format = "csv"
        _parse_table(body, filename, out, mpp)
    return out

