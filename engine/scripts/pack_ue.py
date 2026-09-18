#!/usr/bin/env python3
"""Merge validated full-map detail meshes into two OBJ objects without axis change."""
from __future__ import annotations

import collections
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs"
SOURCE = OUT / "geometry/full/full_details.obj"
MTL = OUT / "geometry/full/full_details.mtl"
DEST = OUT / "UE_Map"
DEST.mkdir(parents=True, exist_ok=True)
report = json.loads((OUT / "reports/full_geometry_report.json").read_text(encoding="utf-8"))
if report.get("status") != "PASS" or report.get("model_sha256") != hashlib.sha256(SOURCE.read_bytes()).hexdigest():
    raise RuntimeError("Full-map geometry validation missing or stale")
manifest = json.loads((OUT / "reports/full_object_manifest.json").read_text())
categories = {record["id"]: record["category"] for record in manifest["objects"]}
site_categories = {"courtyard", "courtyard_wall", "courtyard_paving", "visual_path", "wall_coping", "gate_leaf", "gate_lintel", "gate_pier", "gate_threshold", "gate_trim"}
data = {key: [] for key in ("v", "vt", "vn")}
faces = []
current_object = current_material = None
for line in SOURCE.read_text(encoding="utf-8").splitlines():
    parts = line.split()
    if not parts:
        continue
    if parts[0] in data:
        data[parts[0]].append(line)
    elif parts[0] == "o":
        current_object = parts[1]
    elif parts[0] == "usemtl":
        current_material = parts[1]
    elif parts[0] == "f":
        if current_object not in categories:
            raise ValueError(f"Face belongs to unknown object {current_object}")
        group = "CourtyardDetails" if categories[current_object] in site_categories else "BuildingDetails"
        corners = [tuple(map(int, item.split("/"))) for item in parts[1:]]
        if any(len(corner) != 3 for corner in corners):
            raise ValueError("OBJ requires position/UV/normal per corner")
        faces.append((group, current_material, corners))
if not faces:
    raise ValueError("No detail faces")
groups = collections.Counter(group for group, _, _ in faces)
if set(groups) != {"BuildingDetails", "CourtyardDetails"}:
    raise ValueError(f"Expected both detail groups; got {groups}")
keys = ("v", "vt", "vn")
used = {key: sorted({corner[i] for _, _, face in faces for corner in face}) for i, key in enumerate(keys)}
mapping = {key: {old: index + 1 for index, old in enumerate(used[key])} for key in keys}
target = DEST / "BuildingDetails.obj"
with target.open("w", encoding="utf-8", newline="\n") as stream:
    stream.write("# Original map OBJ coordinates: Z-up, cm, no extra Y reflection\nmtllib BuildingDetails.mtl\n")
    for key in keys:
        for old in used[key]:
            stream.write(data[key][old - 1] + "\n")
    for group in ("BuildingDetails", "CourtyardDetails"):
        stream.write(f"o {group}\ns off\n")
        current = None
        for item_group, material, corners in faces:
            if item_group != group:
                continue
            if material != current:
                stream.write(f"usemtl {material}\n")
                current = material
            stream.write("f " + " ".join("/".join(str(mapping[key][corner[i]]) for i, key in enumerate(keys)) for corner in corners) + "\n")
# Reparse actual exported corners and compare coordinates, UVs, normals and materials.
new_data = {key: [] for key in keys}
new_faces = []
new_group = new_material = None
object_names = []
for line in target.read_text(encoding="utf-8").splitlines():
    parts = line.split()
    if not parts:
        continue
    if parts[0] in new_data:
        new_data[parts[0]].append(line)
    elif parts[0] == "o":
        new_group = parts[1]
        object_names.append(new_group)
    elif parts[0] == "usemtl":
        new_material = parts[1]
    elif parts[0] == "f":
        new_faces.append((new_group, new_material, [tuple(map(int, item.split("/"))) for item in parts[1:]]))
def signatures(records, arrays):
    return collections.Counter((group, material, tuple(tuple(arrays[key][corner[i] - 1] for i, key in enumerate(keys)) for corner in face)) for group, material, face in records)
if object_names != ["BuildingDetails", "CourtyardDetails"] or signatures(faces, data) != signatures(new_faces, new_data):
    raise RuntimeError("Exported OBJ differs from source detail triangles")
shutil.copy2(MTL, DEST / "BuildingDetails.mtl")
textures = SOURCE.parent / "textures"
if textures.is_dir():
    shutil.copytree(textures, DEST / "textures", dirs_exist_ok=True)
for line in (DEST / "BuildingDetails.mtl").read_text(encoding="utf-8").splitlines():
    if line.startswith("map_Kd ") and not (DEST / line[7:]).is_file():
        raise FileNotFoundError(f"Missing texture: {line[7:]}")
payload = {"status": "READY_FOR_UE_IMPORT", "objects": object_names, "triangles": len(faces), "by_object": dict(groups), "source_sha256": report["model_sha256"], "output_sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "triangle_corners_materials_verified": True, "coordinate_transform": "identity", "units": "cm", "up_axis": "Z", "ue_import_verified": False}
(DEST / "coordinate_conversion.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
(DEST / "README_IMPORT.md").write_text("# 建筑细节 UE 导入\n\n导入 BuildingDetails.obj，保持 MTL 和 textures 同级。与原地形 OBJ 使用相同的 UE 导入和放置设置；文件侧不额外翻转 Y、旋转或重新居中。包含两个对象 BuildingDetails 和 CourtyardDetails，不包含原 Building 基础、Ground 或 Water。实际 UE 导入仍需现场核对。\n", encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False))
