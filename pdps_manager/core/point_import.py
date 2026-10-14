"""Pure CSV/Excel row conversion for the point import workflow."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping, Sequence

from .point_report import to_mm_coordinates


def _missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float):
        return math.isnan(value)
    return str(value).strip() == ""


def import_point_rows(
    rows: Sequence[Mapping],
    *,
    source_path: str,
    source_kind: str,
) -> tuple[list[dict], int, list[str]]:
    """Convert external XYZ rows to mm records; return records, failure count, samples.

    Coordinates use the declared ``unit`` (default mm). Columns explicitly
    suffixed with ``_mm`` reject conflicting non-mm unit declarations.
    At most three row errors are returned for concise UI feedback.
    """
    if source_kind not in {"CSV", "Excel"}:
        raise ValueError(f"不支持的点位来源格式: {source_kind}")
    if not rows:
        return [], 0, []

    columns = {}
    for row in rows:
        columns.update({str(key).strip().lower(): key for key in row})
    axes = {
        axis: next((columns[key] for key in (axis, f"{axis}_mm", f"center_{axis}_mm")
                    if key in columns), None)
        for axis in ("x", "y", "z")
    }
    if not all(axes.values()):
        raise ValueError("文件缺少 X/Y/Z 或 center_x_mm/center_y_mm/center_z_mm 列")

    absolute_path = os.path.abspath(source_path)
    imported, errors = [], []
    failures = 0
    for index, row in enumerate(rows):
        normalized = {str(key).strip().lower(): key for key in row}

        def field(key, default=""):
            column = normalized.get(key.lower())
            if column is None or _missing(row[column]):
                return default
            return str(row[column]).strip()

        try:
            source_unit = field("unit", "mm")
            if (any(str(column).strip().lower().endswith("_mm")
                    for column in axes.values())
                    and source_unit.lower() != "mm"):
                raise ValueError("坐标列已标明 mm，但 unit 列不是 mm")
            x, y, z = to_mm_coordinates(
                (row[axes["x"]], row[axes["y"]], row[axes["z"]]), source_unit,
            )
            name = field(
                "name", field("名称", field("proxy_id", field("id", f"Point_{index + 1}"))),
            )
            record = {
                "Class": field("class", "PmWeldPoint"),
                "ExternalId": field("externalid", name),
                "Name": name,
                "Location": f"{x:.3f},{y:.3f},{z:.3f}",
                "SourceLocation": field("location"),
                "X": x, "Y": y, "Z": z,
                "Type": field("type", field("shape_candidate", "RSW")),
                "Source": source_kind,
                "SourcePath": absolute_path,
                "SourceObject": field("source_object", name),
                "CoordinateSystem": field("coordinate_system", "unspecified"),
                "Unit": "mm",
                "SourceUnit": source_unit,
            }
            confidence = field("confidence")
            method = field("extraction_method")
            if confidence:
                record["_Confidence"] = confidence
            if method:
                record["_ExtractionMethod"] = method
            imported.append(record)
        except Exception as exc:
            failures += 1
            if len(errors) < 3:
                errors.append(f"第 {index + 2} 行：{exc}")
    return imported, failures, errors
