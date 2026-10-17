"""Shared business rules for weld layers in lists, views, and exports."""

import re
import unicodedata
from collections import Counter


LAYER_FIELDS = {"type", "weldlayers", "weld_layers", "layercount", "layer_count",
                "layers", "焊点层数", "板层数"}
COUNT_FIELDS = LAYER_FIELDS - {"type"}
SHAPE_FIELDS = {"geometrytype", "geometry_type", "shape", "shape_candidate", "type"}
SHAPE_LAYERS = {
    "正方体": 3, "立方体": 3, "cube": 3,
    "球体": 2, "球形": 2, "sphere": 2, "spherical": 2,
    "圆锥": 4, "圆锥体": 4, "cone": 4, "conical": 4,
}


def weld_layer(row):
    """Recognized cube=3T, cone=4T, sphere=2T; otherwise metadata or 2T.

    Recognized geometry takes precedence over stale layer labels. CSV rows
    without recognized geometry retain unambiguous explicit 2T/3T/4T values.
    Missing/conflicting labels default to 2T. Shape hypotheses, object names,
    and tessellation counts are not recognized geometry.
    """
    shapes = set()
    for key, value in row.items():
        if str(key).strip().casefold() in SHAPE_FIELDS:
            shape = unicodedata.normalize("NFKC", str(value)).strip().casefold()
            if shape in SHAPE_LAYERS:
                shapes.add(SHAPE_LAYERS[shape])
    if len(shapes) == 1:
        return shapes.pop()
    if len(shapes) > 1:
        return 2
    found = set()
    for key, value in row.items():
        key = str(key).strip().casefold()
        if key not in LAYER_FIELDS or value is None:
            continue
        text = unicodedata.normalize("NFKC", str(value)).strip().upper()
        found.update(int(match) for match in re.findall(r"(?<![A-Z0-9])([234])\s*T(?![A-Z0-9])", text))
        for number, chinese in ((2, "二两"), (3, "三"), (4, "四")):
            if re.search(rf"(?<![0-9一二三四五六七八九十百千万两])(?:{number}|[{chinese}])\s*层", text):
                found.add(number)
        if key in COUNT_FIELDS and text in {"2", "2.0", "3", "3.0", "4", "4.0"}:
            found.add(int(float(text)))
    return found.pop() if len(found) == 1 else 2


def layer_counts(rows):
    counts = Counter(weld_layer(row) for row in rows)
    return {layer: counts[layer] for layer in (2, 3, 4)}


def layer_label(layer):
    layer = layer if layer in (2, 3, 4) else 2
    return f"{layer}T / {layer}层板"
