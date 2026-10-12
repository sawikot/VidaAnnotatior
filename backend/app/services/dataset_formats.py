"""Reading the annotation files of image datasets: files that say which image they are about.

``annotation_import`` reads one file for one slide. A dataset is many images with their annotations,
in whatever a labelling tool or a public dataset wrote:

* ``yolo``          one ``.txt`` per image: ``class cx cy w h`` (boxes) or ``class x1 y1 x2 y2 ...``
                    (outlines), all as fractions of the image; class names in ``classes.txt``/``data.yaml``
* ``voc``           Pascal VOC: one ``.xml`` per image with ``<object><bndbox>``
* ``labelme``       LabelMe: one ``.json`` per image with ``shapes``
* ``cvat``          CVAT for images: one ``.xml`` with an ``<image>`` per image
* ``label_studio``  Label Studio JSON export: one task per image, sizes in percent
* ``coco``          COCO: one ``.json`` for all images
* ``table``         a CSV/TSV with a file column: a box, point or WKT per row -- or just a label per image
* ``mask``          a mask image per image: each pixel value (or colour) is a class
* ``single``        a file for one image or slide, named like it, in a format ``annotation_import`` reads

Every reader yields, per image, the file's own name for that image (``ref``) and a function that,
given the image's size, makes its import entries -- the same entries ``annotation_import`` makes, in
the image's own pixels. Which image a ``ref`` is, is the caller's business (services/dataset_import).
"""
from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import cv2
import numpy as np
from PIL import Image
from shapely import make_valid
from shapely.geometry import Polygon, box

from app.services.annotation_import import (
    LABEL_NAMES,
    ImportFormatError,
    ParsedFile,
    _area_shape,
    _box,
    _ellipse_ring,
    _json_format,
    _key,
    _num,
    _open_ring,
    _parse_coco,
    parse_annotation_file,
)

FORMAT_NAMES = {
    "yolo": "YOLO .txt",
    "voc": "Pascal VOC XML",
    "labelme": "LabelMe JSON",
    "cvat": "CVAT XML",
    "label_studio": "Label Studio JSON",
    "coco": "COCO JSON",
    "table": "CSV / TSV table",
    "mask": "Mask images",
    "single": "Annotation file per image",
    "folders": "Folder names",
}

ANNOTATION_EXTENSIONS = {".txt", ".xml", ".json", ".geojson", ".csv", ".tsv", ".yaml", ".yml", ".names"}
MAX_TEXT_BYTES = 200 * 1024 * 1024

_CLASS_LIST_FILES = {"classes.txt", "obj.names", "labels.txt", "label_map.txt", "class_names.txt", "names.txt"}
FILE_COLUMNS = ["filename", "file", "image", "imagename", "imagefile", "imagepath", "imageid", "img", "path", "filepath", "imgname"]
_GEOMETRY_COLUMNS = {"x", "y", "cx", "cy", "xmin", "ymin", "x1", "y1", "left", "top", "wkt", "geometry", "geom", "roi", "bboxx1", "bboxxmin", "xpx", "ypx"}
_YOLO_LINE = re.compile(r"^\d+(\s+[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?){4,}$")


@dataclass
class Size:
    """What a reader may need to know about the image a file is about."""

    width: int | None
    height: int | None
    mpp: tuple[float | None, float | None] = (None, None)


@dataclass
class ImagePart:
    """What one annotation file says about one image."""

    ref: str  # how the file names the image: a file name or path; "" for "the image named like me"
    read: Callable[[Size], ParsedFile]  # its shapes, once the image's size is known
    label: str | None = None  # a label for the image as a whole (classification)


def _text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


# ------------------------------------------------------------------ class lists


def read_class_names(path: Path) -> list[str] | None:
    """The class names a file lists, in order (a YOLO ``classes.txt`` or ``data.yaml``, ``obj.names``,
    Label Studio's ``notes.json``), or None when it is not such a file."""
    name, ext = path.name.lower(), path.suffix.lower()
    try:
        if ext in (".yaml", ".yml"):
            import yaml

            doc = yaml.safe_load(_text(path))
            names = doc.get("names") if isinstance(doc, dict) else None
            if isinstance(names, dict):
                return [str(names[k]) for k in sorted(names, key=lambda k: int(k))]
            return [str(n) for n in names] if isinstance(names, list) else None
        if name == "notes.json":
            doc = json.loads(_text(path))
            cats = doc.get("categories") if isinstance(doc, dict) else None
            if isinstance(cats, list) and all(isinstance(c, dict) and "name" in c for c in cats):
                return [str(c["name"]) for c in sorted(cats, key=lambda c: c.get("id", 0))]
            return None
        if name in _CLASS_LIST_FILES or ext == ".names":
            lines = [line.strip() for line in _text(path).splitlines()]
            return [line for line in lines if line] or None
    except Exception:  # noqa: BLE001 - not a class list after all
        return None
    return None


def _class_label(index: int, names: list[str] | None) -> str:
    return names[index] if names and 0 <= index < len(names) else f"class {index}"


# ------------------------------------------------------------------ what a file is


def sniff(path: Path, image_project: bool) -> tuple[str | None, Any]:
    """(format, the file as already read) of an annotation file; (None, None) when it is not one.
    A COCO file is for all images in an image project, and for the one slide named like it otherwise."""
    ext = path.suffix.lower()
    if ext not in ANNOTATION_EXTENSIONS or path.stat().st_size > MAX_TEXT_BYTES:
        return None, None
    if read_class_names(path) is not None or ext in (".yaml", ".yml", ".names"):
        return None, None  # a class list (read separately), or settings of some tool
    text = _text(path)
    body = text.lstrip()
    if ext == ".txt":
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        if all(_YOLO_LINE.match(line) for line in lines):
            return "yolo", lines  # an empty file: an image with nothing in it
        return None, None
    if not body:
        return None, None
    if body[0] == "<":
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return None, None
        if root.tag == "annotation":
            return "voc", root
        if root.tag == "annotations" and root.find("image") is not None:
            return "cvat", root
        return ("single", text) if root.tag in ("ASAP_Annotations", "Annotations") else (None, None)
    if body[0] in "{[":
        try:
            doc = json.loads(body)
        except json.JSONDecodeError:
            return None, None
        if isinstance(doc, dict) and isinstance(doc.get("shapes"), list) and ("imagePath" in doc or "imageHeight" in doc):
            return "labelme", doc
        first = doc[0] if isinstance(doc, list) and doc and isinstance(doc[0], dict) else None
        if first is not None and isinstance(first.get("data"), dict) and ("annotations" in first or "completions" in first):
            return "label_studio", doc
        try:
            kind = _json_format(doc)
        except ImportFormatError:
            return None, None
        return ("coco", doc) if kind == "coco" and image_project else ("single", text)
    if ext in (".csv", ".tsv"):
        header = next(csv.reader(io.StringIO(body[:20000]), _dialect(body, path.name)), [])
        keys = {_key(h) for h in header}
        if keys & set(FILE_COLUMNS):
            return "table", text
        return ("single", text) if keys & _GEOMETRY_COLUMNS else (None, None)
    return None, None


def _dialect(text: str, filename: str):
    try:
        return csv.Sniffer().sniff(text[:20000], delimiters=",;\t")
    except csv.Error:
        return csv.excel_tab if filename.lower().endswith(".tsv") else csv.excel


# ------------------------------------------------------------------ one file per image


def read_single(path: Path, text: str) -> Iterator[ImagePart]:
    """A file for the one image or slide named like it, in any format ``annotation_import`` reads."""

    def read(size: Size) -> ParsedFile:
        return parse_annotation_file(path.name, text.encode("utf-8"), mpp=size.mpp, slide_size=(size.width, size.height))

    yield ImagePart("", read)


def read_yolo(lines: list[str], names: list[str] | None) -> Iterator[ImagePart]:
    def read(size: Size) -> ParsedFile:
        out = ParsedFile(format="yolo")
        if not size.width or not size.height:
            raise ImportFormatError("YOLO coordinates are fractions of the image, whose size is not known.")
        w, h = size.width, size.height
        for line in lines:
            parts = line.split()
            label = _class_label(int(parts[0]), names)
            values = [float(v) for v in parts[1:]]
            if max(values) > 1.5:
                out.unreadable["coordinates that are not fractions of the image"] += 1
            elif len(values) in (4, 5):  # a box (with a confidence, from a model's predictions)
                cx, cy, bw, bh = values[:4]
                out.add("rectangle", _box((cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w, (cy + bh / 2) * h), label)
            elif len(values) >= 6 and len(values) % 2 == 0:
                ring = [[values[i] * w, values[i + 1] * h] for i in range(0, len(values), 2)]
                out.add("polygon", _open_ring(ring), label)
            else:
                out.unreadable["malformed line"] += 1
        return out

    yield ImagePart("", read)


def read_voc(root: ET.Element) -> Iterator[ImagePart]:
    def read(_size: Size) -> ParsedFile:
        out = ParsedFile(format="voc")
        for obj in root.iter("object"):
            label = (obj.findtext("name") or "").strip() or None
            box, polygon = obj.find("bndbox"), obj.find("polygon")
            try:
                if polygon is not None:  # some tools add the outline: x1, y1, x2, y2, ...
                    points = sorted(polygon, key=lambda e: (int(re.sub(r"\D", "", e.tag) or 0), e.tag[0] != "x"))
                    flat = [_num(e.text) for e in points]
                    out.add("polygon", _open_ring([[flat[i], flat[i + 1]] for i in range(0, len(flat) - 1, 2)]), label)
                elif box is not None:
                    x0, y0, x1, y1 = (_num(box.findtext(tag)) for tag in ("xmin", "ymin", "xmax", "ymax"))
                    out.add("rectangle", _box(x0, y0, x1, y1), label, unsure=obj.findtext("difficult") == "1")
                else:
                    out.unreadable["object without a box"] += 1
            except (TypeError, ValueError):
                out.unreadable["malformed object"] += 1
        return out

    yield ImagePart((root.findtext("filename") or "").strip(), read)


def _labelme_shape(out: ParsedFile, kind: str, points: list, label: str | None) -> None:
    if kind == "rectangle" and len(points) == 2:
        (x0, y0), (x1, y1) = points
        out.add("rectangle", _box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)), label)
    elif kind == "circle" and len(points) == 2:
        out.add("circle", points, label)  # centre and a point on the edge, as here
    elif kind == "point" and len(points) == 1:
        out.add("point", points, label)
    elif kind in ("line", "linestrip") and len(points) >= 2:
        out.add("line", points, label)
    elif kind in ("polygon", "") and len(points) >= 3:
        shape_type, ring = _area_shape(_open_ring(points))
        out.add(shape_type, ring, label)
    else:
        out.unreadable[f"{kind or 'unknown'} shape"] += 1


def read_labelme(doc: dict) -> Iterator[ImagePart]:
    def read(_size: Size) -> ParsedFile:
        out = ParsedFile(format="labelme")
        for shape in doc["shapes"]:
            if not isinstance(shape, dict) or not isinstance(shape.get("points"), list):
                out.unreadable["malformed shape"] += 1
                continue
            _labelme_shape(out, str(shape.get("shape_type") or "polygon"), shape["points"], shape.get("label"))
        return out

    yield ImagePart(str(doc.get("imagePath") or ""), read)


# ------------------------------------------------------------------ one file for many images


def _points(text: str) -> list[list[float]]:
    return [[float(v) for v in pair.split(",")[:2]] for pair in text.split(";") if pair.strip()]


def read_cvat(root: ET.Element) -> Iterator[ImagePart]:
    for image in root.iter("image"):

        def read(_size: Size, image: ET.Element = image) -> ParsedFile:
            out = ParsedFile(format="cvat")
            for el in image:
                label, a = el.get("label"), el.attrib
                try:
                    if el.tag == "box":
                        out.add("rectangle", _box(float(a["xtl"]), float(a["ytl"]), float(a["xbr"]), float(a["ybr"])), label)
                    elif el.tag == "polygon":
                        shape_type, ring = _area_shape(_open_ring(_points(a["points"])))
                        out.add(shape_type, ring, label)
                    elif el.tag == "polyline":
                        out.add("line", _points(a["points"]), label)
                    elif el.tag == "points":
                        for point in _points(a["points"]):
                            out.add("point", [point], label)
                    elif el.tag == "ellipse":
                        cx, cy, rx, ry = (float(a[k]) for k in ("cx", "cy", "rx", "ry"))
                        if abs(rx - ry) < 0.5:
                            out.add("circle", [[cx, cy], [cx + rx, cy]], label)
                        else:
                            out.add("polygon", _open_ring(_ellipse_ring(cx - rx, cy - ry, cx + rx, cy + ry)), label)
                    elif el.tag not in ("tag", "attribute"):
                        out.unreadable[f"{el.tag} shape"] += 1
                except (KeyError, ValueError):
                    out.unreadable["malformed shape"] += 1
            return out

        tags = [el.get("label") for el in image if el.tag == "tag" and el.get("label")]
        yield ImagePart(image.get("name") or "", read, label=tags[0] if len(tags) == 1 else None)


def read_label_studio(doc: list) -> Iterator[ImagePart]:
    for task in doc:
        if not isinstance(task, dict) or not isinstance(task.get("data"), dict):
            continue
        ref = next((str(v) for v in task["data"].values() if isinstance(v, str) and re.search(r"\.(png|jpe?g|tiff?|bmp|webp|gif|svs|ndpi)(\?|$)", v, re.I)), "")
        done = task.get("annotations") or task.get("completions") or []
        results = [r for a in done[:1] if isinstance(a, dict) for r in a.get("result") or [] if isinstance(r, dict)]
        choices = [c for r in results for c in (r.get("value") or {}).get("choices") or []]

        def read(size: Size, results: list = results) -> ParsedFile:
            out = ParsedFile(format="label_studio")
            for r in results:
                v = r.get("value") or {}
                w, h = r.get("original_width") or size.width, r.get("original_height") or size.height
                label = next((v[k][0] for k in ("rectanglelabels", "polygonlabels", "keypointlabels", "ellipselabels", "labels", "brushlabels") if v.get(k)), None)
                if "choices" in v:
                    continue  # a label for the whole image
                if not w or not h:
                    out.unreadable["shape without an image size"] += 1
                    continue
                sx, sy = w / 100, h / 100  # Label Studio stores percent of the image
                try:
                    if "points" in v:
                        shape_type, ring = _area_shape(_open_ring([[x * sx, y * sy] for x, y in v["points"]]))
                        out.add(shape_type, ring, label)
                    elif "radiusX" in v:
                        cx, cy, rx, ry = v["x"] * sx, v["y"] * sy, v["radiusX"] * sx, v["radiusY"] * sy
                        out.add("polygon", _open_ring(_ellipse_ring(cx - rx, cy - ry, cx + rx, cy + ry)), label)
                    elif "width" in v and "height" in v and r.get("type") != "keypointlabels":
                        if v.get("rotation"):
                            out.warn("Rotated Label Studio boxes were imported unrotated.")
                        out.add("rectangle", _box(v["x"] * sx, v["y"] * sy, (v["x"] + v["width"]) * sx, (v["y"] + v["height"]) * sy), label)
                    elif "x" in v and "y" in v:
                        out.add("point", [[v["x"] * sx, v["y"] * sy]], label)
                    else:
                        out.unreadable[f"{r.get('type') or 'unknown'} result"] += 1
                except (KeyError, TypeError, ValueError):
                    out.unreadable["malformed result"] += 1
            return out

        yield ImagePart(unquote(ref.split("?")[0]), read, label=str(choices[0]) if len(choices) == 1 else None)


def read_coco(doc: dict) -> Iterator[ImagePart]:
    """COCO with one entry per image of the dataset: each image's annotations, in its own pixels."""
    by_image: dict[Any, list] = {}
    for ann in doc.get("annotations") or []:
        if isinstance(ann, dict):
            by_image.setdefault(ann.get("image_id"), []).append(ann)
    categories = doc.get("categories") or []
    for image in doc.get("images") or []:
        if not isinstance(image, dict):
            continue

        def read(size: Size, image: dict = image) -> ParsedFile:
            out = ParsedFile(format="coco")
            # As a COCO file of its own. This app's export says where its image is (and keeps that);
            # any other image is simply itself, whatever its file name looks like.
            own = image if "vp_origin_level0" in image else {k: image.get(k) for k in ("id", "width", "height")}
            _parse_coco({"images": [own], "annotations": by_image.get(image.get("id"), []), "categories": categories}, out, (size.width, size.height))
            return out

        yield ImagePart(str(image.get("file_name") or ""), read)


def read_table(text: str, filename: str) -> Iterator[ImagePart]:
    """A table with a file column: each image's rows read as a table of its own -- or, with no
    coordinates at all, a label per image."""
    dialect = _dialect(text, filename)
    reader = csv.reader(io.StringIO(text), dialect)
    header = next(reader, [])
    keys = [_key(h) for h in header]
    file_at = next(i for name in FILE_COLUMNS for i, key in enumerate(keys) if key == name)
    label_at = next((i for name in LABEL_NAMES + ["target", "diagnosis", "grade"] for i, key in enumerate(keys) if key == name and i != file_at), None)
    has_shapes = bool(set(keys) & _GEOMETRY_COLUMNS)
    groups: dict[str, list[list[str]]] = {}
    for row in reader:
        if len(row) > file_at and row[file_at].strip():
            groups.setdefault(row[file_at].strip(), []).append(row)

    for ref, rows in groups.items():
        labels = {row[label_at].strip() for row in rows if label_at is not None and len(row) > label_at and row[label_at].strip()}

        def read(size: Size, rows: list = rows) -> ParsedFile:
            if not has_shapes:
                return ParsedFile(format="table")
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(header)
            writer.writerows(rows)
            out = parse_annotation_file("rows.csv", buffer.getvalue().encode("utf-8"), mpp=size.mpp, slide_size=(size.width, size.height))
            out.format = "table"
            return out

        yield ImagePart(ref, read, label=labels.pop() if not has_shapes and len(labels) == 1 else None)


# ------------------------------------------------------------------ masks

MASK_FOLDERS = {
    "mask", "masks", "label", "labels", "annotation", "annotations", "ano", "anno", "annos", "annot", "segmentation", "segmentations", "seg",
    "segmentationclass", "segmentationclassraw", "segmentationobject", "gt", "groundtruth", "ground_truth", "target", "targets",
}
_MASK_SUFFIX = re.compile(r"[-_. ](mask|masks|label|labels|seg|segmentation|gt|annotation|anno)$", re.I)
_MAX_MASK_CLASSES = 64
_MAX_MASK_SHAPES = 20000
# A slide's mask is read at its finest level of at most this many pixels: fine enough for outlines
# of tissue, without holding a gigapixel image in memory.
_SLIDE_MASK_PIXELS = 80_000_000
_STRIP_ROWS = 2048


def mask_stem(stem: str) -> tuple[str, bool]:
    """(the stem of the image a mask file is of, whether its own name says it is a mask)."""
    stripped = _MASK_SUFFIX.sub("", stem)
    return stripped, stripped != stem


def _polygons(geometry) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [geometry] if geometry.area > 1e-9 else []
    return [poly for part in getattr(geometry, "geoms", []) for poly in _polygons(part)]


def _hole_free(polygon: Polygon) -> Iterator[Polygon]:
    """``polygon`` as pieces without holes, together covering exactly what it covers. An outline here
    is a single ring, so an area with holes (tissue around glands) is cut -- straight across, through
    a hole near its middle, and each side again -- until no piece has one."""
    stack = [polygon]
    while stack:
        piece = stack.pop()
        if not piece.interiors:
            yield piece
            continue
        min_x, min_y, max_x, max_y = piece.bounds
        across = (max_x - min_x) >= (max_y - min_y)  # cut the longer way in two
        inside = sorted((Polygon(hole).representative_point() for hole in piece.interiors), key=lambda pt: pt.x if across else pt.y)
        at = inside[len(inside) // 2]
        halves = (
            (box(min_x, min_y, at.x, max_y), box(at.x, min_y, max_x, max_y)) if across else (box(min_x, min_y, max_x, at.y), box(min_x, at.y, max_x, max_y))
        )
        for half in halves:
            stack += _polygons(piece.intersection(half))


def _mask_outlines(pixels: np.ndarray, label_of: dict[int, str], out: ParsedFile, min_area: float) -> None:
    """Every connected area of each value of ``pixels`` as outlines (in pixels; ``out.scale`` takes
    them to the image's own). They cover the area's pixels edge to edge, holes left open."""
    shapes = 0
    for value, label in label_of.items():
        contours, hierarchy = cv2.findContours((pixels == value).astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if hierarchy is None:
            continue
        holes_of: dict[int, list] = {}
        for index, (_next, _prev, _child, parent) in enumerate(hierarchy[0]):
            if parent != -1 and cv2.contourArea(contours[index]) >= min_area:
                holes_of.setdefault(parent, []).append(contours[index])
        for index, (_next, _prev, _child, parent) in enumerate(hierarchy[0]):
            if parent != -1 or cv2.contourArea(contours[index]) < min_area:
                continue  # a hole (taken with its area), or a speck: a stray pixel or two

            def ring(contour) -> list:
                return (cv2.approxPolyDP(contour, 0.75, True)[:, 0, :] + 0.5).tolist()

            outer, holes = ring(contours[index]), [r for r in map(ring, holes_of.get(index, [])) if len(r) >= 3]
            if len(outer) < 3:
                continue
            area = Polygon(outer, holes)
            # A contour runs through pixel centres; the area it stands for reaches half a pixel further.
            area = (area if area.is_valid else make_valid(area)).buffer(0.5, join_style="mitre")
            for part in _polygons(area):
                for piece in _hole_free(part):
                    if shapes >= _MAX_MASK_SHAPES:
                        out.unreadable[f"areas beyond the first {_MAX_MASK_SHAPES}"] += 1
                        continue
                    shapes += 1
                    out.add("polygon", list(piece.exterior.coords)[:-1], label)


def _mask_labels(values: list[int], colours: bool, names: list[str] | None) -> dict[int, str]:
    if len(values) > _MAX_MASK_CLASSES:
        raise ImportFormatError(f"The image has {len(values)} different values; it does not look like a class mask.")
    if colours:
        return {v: f"#{v:06x}" for v in values}
    # Black and white: one class, not "class 255". (A lone 1 is class 1: other masks of the set may have more.)
    return {v: "foreground" if values == [255] and not names else _class_label(v, names) for v in values}


def _fit(width: int, height: int, size: Size, scale: float | None, fit: bool) -> tuple[float, str | None]:
    """(what a mask's pixels are multiplied by to be its image's, why that is not 1). A mask is as
    large as its image; with ``fit`` it may be a smaller copy of the same shape (or ``scale`` given)."""
    if scale is not None:
        return scale, None
    if not size.width or not size.height or (width, height) == (size.width, size.height):
        return 1.0, None
    rx, ry = size.width / width, size.height / height
    if fit and rx > 1 and abs(rx - ry) / rx < 0.02:
        factor = round((rx + ry) / 2, 4)
        return factor, f"The mask is {width} × {height} px, the whole image {factor:g}× smaller, so its outlines were scaled up to full size."
    raise ImportFormatError(f"The mask is {width} x {height} px but its image is {size.width} x {size.height} px.")


def read_mask(path: Path, names: list[str] | None, *, fit: bool = False, scale: float | None = None) -> Iterator[ImagePart]:
    """A mask image: every pixel value other than 0 (or colour other than black) is a class, and every
    connected area of it an outline (an area with holes: several, see ``_hole_free``)."""

    def read(size: Size) -> ParsedFile:
        with Image.open(path) as image:
            if image.width * image.height > _SLIDE_MASK_PIXELS:
                raise ImportFormatError("The mask is too large to read as a plain image; save it as a pyramidal TIFF.")
            indexed = image.mode in ("P", "L", "1", "I", "I;16", "F")
            pixels = np.array(image if indexed else image.convert("RGB"))
        factor, note = _fit(pixels.shape[1], pixels.shape[0], size, scale, fit)
        out = ParsedFile(format="mask", scale=factor)
        pixels, colours = _class_numbers(pixels)
        values = [int(v) for v in np.unique(pixels) if v != 0]
        _mask_outlines(pixels, _mask_labels(values, colours, names), out, min_area=4)
        out.scale_note = note
        return out

    yield ImagePart("", read)


def _class_numbers(pixels: np.ndarray) -> tuple[np.ndarray, bool]:
    """(one number per pixel, whether the numbers are colours). A grey image, or one whose classes
    are numbers in the red channel alone (as PANDA's masks are), gives the numbers themselves."""
    if pixels.ndim == 2:
        return pixels.astype(np.int32), False
    red, green, blue = pixels[..., 0], pixels[..., 1], pixels[..., 2]
    if not green.any() and not blue.any() or (np.array_equal(red, green) and np.array_equal(red, blue)):
        return red.astype(np.int32), False
    return (red.astype(np.int32) << 16) | (green.astype(np.int32) << 8) | blue.astype(np.int32), True


def slide_mask_size(path: Path) -> tuple[int, int] | None:
    """The Level-0 size of a slide file if it is a class mask rather than a picture of tissue (a
    handful of flat values, where a scan has thousands of colours); None otherwise."""
    from app.services.wsi_reader import OpenSlideReader

    try:
        reader = OpenSlideReader(path)
    except Exception:  # noqa: BLE001
        return None
    try:
        meta = reader.get_metadata()
        width, height = meta.level_dimensions[-1]
        if width * height > _SLIDE_MASK_PIXELS:
            return None
        numbers, _ = _class_numbers(np.array(reader.read_region(0, 0, meta.level_count - 1, width, height)))
        return (meta.width, meta.height) if len(np.unique(numbers)) <= _MAX_MASK_CLASSES else None
    except Exception:  # noqa: BLE001
        return None
    finally:
        reader.close()


def read_slide_mask(path: Path, names: list[str] | None, *, fit: bool = False, scale: float | None = None) -> Iterator[ImagePart]:
    """A mask as large as its slide, stored like one (a pyramidal TIFF): read at the finest level
    that fits in memory, its outlines scaled up to the slide's own pixels."""

    def read(size: Size) -> ParsedFile:
        from app.services.wsi_reader import OpenSlideReader

        reader = OpenSlideReader(path)
        try:
            meta = reader.get_metadata()
            factor, note = _fit(meta.width, meta.height, size, scale, fit)
            level = next((i for i, (w, h) in enumerate(meta.level_dimensions) if w * h <= _SLIDE_MASK_PIXELS), None)
            if level is None:
                raise ImportFormatError("The mask has no level small enough to read; save it with a pyramid (smaller copies of itself).")
            width, height = meta.level_dimensions[level]
            downsample = meta.level_downsamples[level]
            # Numbers or colours: told once, from the smallest copy, so every strip is read the same way.
            small_w, small_h = meta.level_dimensions[-1]
            colours = _class_numbers(np.array(reader.read_region(0, 0, meta.level_count - 1, small_w, small_h)))[1]
            strips = []
            for top in range(0, height, _STRIP_ROWS):  # in strips: a whole level at once is hundreds of MB as RGB
                strip = np.array(reader.read_region(0, round(top * downsample), level, width, min(_STRIP_ROWS, height - top)))
                if colours:
                    strips.append((strip[..., 0].astype(np.int32) << 16) | (strip[..., 1].astype(np.int32) << 8) | strip[..., 2].astype(np.int32))
                else:
                    strips.append(strip[..., 0].copy())
        finally:
            reader.close()
        pixels = np.vstack(strips)
        out = ParsedFile(format="mask", scale=downsample * factor)
        values = [int(v) for v in np.unique(pixels) if v != 0]
        _mask_outlines(pixels, _mask_labels(values, colours, names), out, min_area=4)
        if level:
            out.warn(f"Slide masks were read at 1/{downsample:g} of full size; their outlines are that exact.")
        out.scale, out.scale_note = factor, note  # what the file's own pixels were multiplied by
        return out

    yield ImagePart("", read)


_IMAGE_MAGIC = (b"\x89PNG", b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+", b"BM", b"GIF8", b"\xff\xd8\xff", b"RIFF")
_IMAGE_SUFFIXES = {".png", ".tif", ".tiff", ".bmp", ".gif", ".jpg", ".jpeg", ".webp", ".svs", ".ndpi", ".scn", ".mrxs", ".bif"}


def is_image_file(filename: str, raw: bytes) -> bool:
    """Whether an uploaded annotation file is a picture (so: a mask), not text."""
    return raw.startswith(_IMAGE_MAGIC) or Path(filename).suffix.lower() in _IMAGE_SUFFIXES


def read_mask_file(path: Path, size: Size, scale: float | None = None) -> ParsedFile:
    """A mask for one image or slide, whichever way it is stored: like a slide (a pyramidal TIFF) or
    as a plain image -- the size of its image, or a smaller copy of the same shape (its outlines are
    then scaled up; ``scale`` says by how much when that is not to be worked out)."""
    from app.services.wsi_reader import OpenSlideReader

    try:
        OpenSlideReader(path).close()
        like_a_slide = True
    except Exception:  # noqa: BLE001 - not a slide file: a plain image, or nothing readable
        like_a_slide = False
    if like_a_slide:
        if slide_mask_size(path) is None:
            raise ImportFormatError("This file is a picture of tissue, not a mask: a mask has a few flat values, one per class.")
        out = next(read_slide_mask(path, None, fit=True, scale=scale)).read(size)
    else:
        try:
            out = next(read_mask(path, None, fit=True, scale=scale)).read(size)
        except ImportFormatError:
            raise
        except Exception as exc:  # noqa: BLE001 - PIL raises many types; all mean "not usable"
            raise ImportFormatError(f"The file could not be read as an image ({exc.__class__.__name__}).") from None
    out.auto_scale = scale is None
    return out
