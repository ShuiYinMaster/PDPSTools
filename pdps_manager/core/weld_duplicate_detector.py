"""Spatial duplicate detection shared by the weld-point GUI workflows.

The first point (or the higher-confidence extraction result) represents each
5 mm neighborhood. Only retained representatives can absorb another point;
this avoids accidentally merging a chain of points more than 5 mm apart.
"""

from __future__ import annotations

import itertools
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class WeldDuplicate:
    duplicate_index: int
    kept_index: int
    distance_mm: float


def _priority(row: Mapping) -> tuple[int, int]:
    confidence = str(row.get("_Confidence") or row.get("Confidence") or "").lower()
    method = str(row.get("_ExtractionMethod") or row.get("ExtractionMethod") or "")
    confidence_rank = {"high": 3, "medium": 2, "low": 1}.get(confidence, 0)
    method_rank = (
        2 if method == "xml_mesh_feature_matching"
        else 0 if method == "binary_tessellation_signature_fallback"
        else 1
    )
    return confidence_rank, method_rank


def find_weld_duplicates(
    rows: Sequence[Mapping], threshold_mm: float = 5.0
) -> tuple[list[WeldDuplicate], list[int]]:
    """Return duplicate matches and invalid-coordinate row indices.

    Distances are Euclidean in X/Y/Z, in millimeters, and inclusive of the
    threshold. Source IDs and displayed names do not determine equality.
    """
    if not math.isfinite(threshold_mm) or threshold_mm <= 0:
        raise ValueError("重复点阈值必须是大于 0 的有限毫米数")

    coordinates: dict[int, tuple[float, float, float]] = {}
    invalid_indices: list[int] = []
    for index, row in enumerate(rows):
        try:
            point = tuple(float(row[axis]) for axis in ("X", "Y", "Z"))
            if not all(math.isfinite(value) for value in point):
                raise ValueError("non-finite coordinate")
            coordinates[index] = point
        except (KeyError, TypeError, ValueError):
            invalid_indices.append(index)

    cells: dict[tuple[int, int, int], list[int]] = defaultdict(list)
    matches: list[WeldDuplicate] = []
    order = sorted(coordinates, key=lambda index: tuple(-p for p in _priority(rows[index])) + (index,))
    for index in order:
        point = coordinates[index]
        cell = tuple(math.floor(value / threshold_mm) for value in point)
        nearest: tuple[float, int] | None = None
        for offsets in itertools.product((-1, 0, 1), repeat=3):
            neighbor = tuple(cell[axis] + offsets[axis] for axis in range(3))
            for kept_index in cells.get(neighbor, ()):
                distance = math.dist(point, coordinates[kept_index])
                if distance <= threshold_mm and (nearest is None or (distance, kept_index) < nearest):
                    nearest = (distance, kept_index)
        if nearest is None:
            cells[cell].append(index)
        else:
            matches.append(WeldDuplicate(index, nearest[1], round(nearest[0], 6)))
    matches.sort(key=lambda match: match.duplicate_index)
    return matches, invalid_indices
