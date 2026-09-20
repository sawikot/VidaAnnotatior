"""Export format registry. Each format is one small class; adding another means
writing an ``Exporter`` subclass and listing it here -- callers only ever go
through ``get_exporter``."""
from __future__ import annotations

from .base import ExportData, Exporter, dumps_with_line_items, load_export_data
from .coco import COCOExporter
from .geojson import GeoJSONExporter
from .tables import PatchCSVExporter, StatsCSVExporter
from .wsi_json import WSIJSONExporter

REGISTRY: dict[str, Exporter] = {
    e.format_id: e
    for e in (WSIJSONExporter(), GeoJSONExporter(), COCOExporter(), PatchCSVExporter(), StatsCSVExporter())
}


def get_exporter(format_id: str) -> Exporter:
    if format_id not in REGISTRY:
        raise ValueError(f"Unknown export format '{format_id}'. Available: {', '.join(REGISTRY)}")
    return REGISTRY[format_id]


__all__ = ["REGISTRY", "ExportData", "Exporter", "dumps_with_line_items", "get_exporter", "load_export_data"]
