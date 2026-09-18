#!/usr/bin/env python3
"""Validate a fresh high-level task list against the complete Building Label."""
from __future__ import annotations

import json
import importlib.util
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from shapely.geometry import Point, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
OUT = ROOT / "outputs"
CFG=json.loads((ROOT/'run_config.json').read_text(encoding='utf-8'))
sp=importlib.util.spec_from_file_location('map_common',(ROOT/CFG['inputs']['map_common']).resolve());mc=importlib.util.module_from_spec(sp);sp.loader.exec_module(mc)
tasks = json.loads((OUT / "specs/tasks.json").read_text(encoding="utf-8"))
allowed = shape(json.loads((OUT / "audit/label40.geojson").read_text(encoding="utf-8")))
sites = tasks["sites"]
buildings = tasks["buildings"]
gates = tasks["gates"]
wall = shape(tasks["wall_network_px1"])
issues = []
ids = [x["id"] for group in (sites, buildings, gates) for x in group]
if len(ids) != len(set(ids)):
    issues.append({"kind": "duplicate_ids"})
site_ids = {s["id"] for s in sites}
for s in sites:
    p = shape(s["footprint_px1"])
    if not allowed.covers(p):
        issues.append({"id": s["id"], "kind": "site_outside_label"})
for b in buildings:
    if b["court_id"] not in site_ids:
        issues.append({"id": b["id"], "kind": "missing_site"})
    p = shape(b["envelope_px1"])
    if not allowed.covers(p):
        issues.append({"id": b["id"], "kind": "complete_envelope_outside_label"})
for i, a in enumerate(buildings):
    ap = shape(a["footprint_px1"])
    for b in buildings[i + 1:]:
        if ap.intersection(shape(b["footprint_px1"])).area > 1e-7:
            issues.append({"id": a["id"], "other": b["id"], "kind": "building_overlap"})
if not allowed.covers(wall):
    issues.append({"kind": "wall_outside_label"})
if any(wall.intersection(shape(b["footprint_px1"])).area > 1e-7 for b in buildings):
    issues.append({"kind": "wall_crosses_building"})
for g in gates:
    if g["court_id"] not in site_ids:
        issues.append({"id": g["id"], "kind": "missing_site"})
    if wall.intersects(Point(g["center_px1"]).buffer(g["width_m"] / 2 / mc.PX_M)):
        issues.append({"id": g["id"], "kind": "gate_blocked"})
if not tasks.get("representative_site_ids"):
    issues.append({"kind": "missing_representative_sites"})

report = {"stage": 2, "status": "PASS" if not issues else "FAIL", "sites": len(sites), "buildings": len(buildings), "gates": len(gates), "issues": issues}
(OUT / "audit/layout_check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
with Image.open((ROOT/CFG['inputs']['satellite']).resolve()) as source:
    image = source.convert("RGB").resize((2048, 2048))
draw = ImageDraw.Draw(image, "RGBA")
def outline(geom, color):
    for poly in getattr(geom, "geoms", [geom]):
        if poly.geom_type == "Polygon":
            draw.line([(x * 2, y * 2) for x, y in poly.exterior.coords], fill=color, width=2)
outline(allowed, (20, 230, 80, 220))
for s in sites:
    outline(shape(s["footprint_px1"]), (240, 195, 70, 220))
for b in buildings:
    outline(shape(b["footprint_px1"]), (230, 90, 45, 240))
outline(wall, (60, 50, 45, 230))
image.save(OUT / "audit/layout_overview.png")
print(json.dumps(report, ensure_ascii=False))
if issues:
    raise SystemExit(1)
