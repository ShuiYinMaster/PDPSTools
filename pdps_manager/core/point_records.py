"""Helpers for keeping extracted point coordinates and provenance consistent."""

from __future__ import annotations

from collections.abc import Mapping

from .point_report import to_mm_coordinates


def canonicalize_point_record(
    row: Mapping,
    *,
    default_source: str = "CATIA",
    source_path: str = "",
    coordinate_system: str = "assembly",
) -> dict:
    """Copy a point row and normalize its coordinates and provenance to the app schema.

    Existing ``Unit=mm`` means X/Y/Z are already normalized, even when
    ``SourceUnit`` records the original input unit. Invalid coordinates remain
    untouched so the existing table can display and report them as invalid.
    """
    result = dict(row)
    unit = str(result.get("Unit") or result.get("SourceUnit") or "mm").strip()
    try:
        xyz = to_mm_coordinates(
            (result["X"], result["Y"], result["Z"]),
            unit="mm" if str(result.get("Unit") or "").lower() == "mm" else unit,
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        xyz = None
    if xyz is not None:
        result.update(zip(("X", "Y", "Z"), xyz))
        result["Location"] = ",".join(f"{value:.3f}" for value in xyz)

    result["Source"] = (result.get("Source") or result.get("_SourceKind")
                        or default_source)
    result["SourcePath"] = (result.get("SourcePath") or result.get("_SourcePath")
                            or result.get("_source_path")
                            or result.get("_CATPartInstance") or source_path)
    result["SourceObject"] = (result.get("SourceObject") or result.get("_OriginalName")
                              or result.get("Name") or "")
    result["CoordinateSystem"] = result.get("CoordinateSystem") or coordinate_system
    result["SourceUnit"] = result.get("SourceUnit") or unit or "mm"
    result["Unit"] = "mm"
    result["Confidence"] = (result.get("Confidence") or result.get("_Confidence") or "")
    result["ExtractionMethod"] = (result.get("ExtractionMethod")
                                  or result.get("_ExtractionMethod") or "")
    return result
