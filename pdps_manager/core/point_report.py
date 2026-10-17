"""Build reviewable weld-point exports without changing the source rows."""

from __future__ import annotations

import math
from typing import Mapping, Sequence

from .weld_duplicate_detector import find_weld_duplicates
from .weld_layers import weld_layer


def to_mm_coordinates(values, unit: str = "mm") -> tuple[float, float, float]:
    """Convert an imported XYZ row to finite millimeter coordinates."""
    scale = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}.get(
        str(unit).strip().lower())
    if scale is None:
        raise ValueError(f"不支持的坐标单位: {unit}")
    point = tuple(float(value) * scale for value in values)
    if len(point) != 3 or not all(math.isfinite(value) for value in point):
        raise ValueError("坐标必须是三个有限数值")
    return point


def build_point_report(rows: Sequence[Mapping], threshold_mm: float = 5.0,
                       selection: str = "all") -> list[dict]:
    """Return all, retained, or duplicate rows with source and match details."""
    if selection not in {"all", "retained", "duplicates"}:
        raise ValueError(f"未知点位导出范围: {selection}")
    matches, invalid = find_weld_duplicates(rows, threshold_mm)
    duplicate_by_index = {match.duplicate_index: match for match in matches}
    kept_with_duplicates = {match.kept_index for match in matches}
    invalid_set = set(invalid)
    report = []
    for index, row in enumerate(rows):
        match = duplicate_by_index.get(index)
        if selection == "retained" and (match or index in invalid_set):
            continue
        if selection == "duplicates" and not match:
            continue
        output = {key: value for key, value in row.items() if not key.startswith("_")}
        output.update({
            "WeldLayers": f"{weld_layer(row)}T",
            "Source": row.get("Source") or row.get("_SourceKind") or "CATIA",
            "SourcePath": row.get("SourcePath") or row.get("_SourcePath")
                          or row.get("_source_path") or row.get("_CATPartInstance") or "",
            "SourceObject": row.get("SourceObject") or row.get("_OriginalName")
                            or row.get("Name") or "",
            "CoordinateSystem": row.get("CoordinateSystem") or "assembly",
            "Unit": row.get("Unit") or "mm",
            "Confidence": row.get("Confidence") or row.get("_Confidence") or "",
            "ExtractionMethod": row.get("ExtractionMethod")
                                or row.get("_ExtractionMethod") or "",
            "SourceRow": index + 1,
            "DuplicateStatus": (
                "duplicate" if match else "invalid" if index in invalid_set
                else "representative" if index in kept_with_duplicates else "unique"
            ),
            "RepresentativeRow": match.kept_index + 1 if match else "",
            "RepresentativeName": rows[match.kept_index].get("Name", "") if match else "",
            "DistanceMM": match.distance_mm if match else "",
            "ThresholdMM": threshold_mm,
        })
        report.append(output)
    return report
