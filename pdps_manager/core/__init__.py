# -*- coding: utf-8 -*-
"""Business logic with lazy exports so pure parsers need no GUI imports."""

from importlib import import_module


_EXPORTS = {
    "CATIAWeldPointExtractor": ("weld_extractor", "CATIAWeldPointExtractor"),
    "CATIASphereScanner": ("sphere_scanner", "CATIASphereScanner"),
    "ThreeDXmlCenterExtractor": ("threedxml_center_extractor", "ThreeDXmlCenterExtractor"),
    "extract_3dxml_centers": ("threedxml_center_extractor", "extract_3dxml_centers"),
    "CatiaGunPlacer": ("gun_placer", "CatiaGunPlacer"),
    "CATIAStudPointExtractor": ("stud_point_extractor", "CATIAStudPointExtractor"),
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attribute = _EXPORTS[name]
    value = getattr(import_module(f".{module_name}", __name__), attribute)
    globals()[name] = value
    return value
