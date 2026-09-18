#!/usr/bin/env python3
"""Stage 0 deterministic raster, source mesh and coordinate audit."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.features import shapes
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
OUT = ROOT / "outputs/audit"
OUT.mkdir(parents=True, exist_ok=True)
CFG = json.loads((ROOT / "run_config.json").read_text(encoding="utf-8"))


def path(key: str) -> Path:
    value = Path(CFG["inputs"][key])
    return (value if value.is_absolute() else ROOT / value).resolve()


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def save(name: str, data: dict) -> None:
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


module = importlib.util.spec_from_file_location("map_common", path("map_common"))
mc = importlib.util.module_from_spec(module)
module.loader.exec_module(mc)
with rasterio.open(path("satellite")) as satellite:
    sat_shape = (satellite.height, satellite.width)
    sat_bounds, sat_crs = satellite.bounds, str(satellite.crs)
with rasterio.open(path("label")) as labels:
    label = labels.read(1)
    label_shape = label.shape
    label_bounds, label_crs = labels.bounds, str(labels.crs)
    scale = int(mc.SCALE)
    if label_shape != tuple(x * scale for x in sat_shape):
        raise ValueError(f"Label shape {label_shape} differs from satellite {sat_shape} x {scale}")
    if sat_crs != label_crs or any(abs(a - b) > 1e-7 for a, b in zip(sat_bounds, label_bounds)):
        raise ValueError("Satellite and Label CRS/bounds differ")
    mask = label == 40
    if not mask.any():
        raise ValueError("No Building Label 40 pixels")
    allowed = unary_union([shape(geom) for geom, value in shapes(mask.astype("uint8"), mask=mask, transform=Affine.scale(1 / scale))])
    save("label40.geojson", mapping(allowed))

vertices = []
faces = 0
with path("base_obj").open(encoding="utf-8-sig") as stream:
    for line in stream:
        if line.startswith("v "):
            vertices.append(tuple(map(float, line.split()[1:4])))
        elif line.startswith("f "):
            faces += 1
v = np.asarray(vertices)
if not len(v) or not faces:
    raise ValueError("Original Building OBJ has no mesh")
anchors = []
h, w = label_shape
for index in sorted({int(np.argmin(v[:, 0] + v[:, 1])), int(np.argmax(v[:, 0])), int(np.argmax(v[:, 1]))}):
    x, y, z = v[index]
    col = x / mc.UE_PER_PX10 + w / 2
    row = h / 2 - y / mc.UE_PER_PX10
    x2, y2 = mc.ue_x_of_col(col, w), mc.ue_y_obj_of_row(row, h)
    error = float(np.hypot(x2 - x, y2 - y))
    if error > 1e-3:
        raise ValueError(f"OBJ coordinate round trip error {error} cm")
    anchors.append({"vertex_index": index, "xyz_cm": [x, y, z], "label_col_row": [col, row], "roundtrip_error_cm": error})

inputs = {key: {"path": str(path(key)), "sha256": sha(path(key))} for key in ("satellite", "label", "base_obj", "base_mtl", "ground_obj", "ground_mtl", "map_common")}
save("input_audit.json", {**inputs, "status": "PASS", "satellite_shape": sat_shape, "label_shape": label_shape, "building_label": 40, "source_faces": faces, "source_vertices": len(v), "coordinate_anchors": anchors, "units": "cm", "up_axis": "Z", "obj_y_reflection": False})
print(json.dumps({"status": "PASS", "label_shape": label_shape, "building_pixels": int(mask.sum()), "source_faces": faces}, ensure_ascii=False))
