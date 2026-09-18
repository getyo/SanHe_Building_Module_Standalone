"""Material renders for Stage 3, fixed full-map review and seeded random review."""
from __future__ import annotations

import hashlib
import json
import math
import random
import sys
import time
import traceback
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
OUT = ROOT / "outputs"
ARGS = sys.argv[sys.argv.index("--") + 1:]
SCOPE, SET = ARGS[0], ARGS[1]
REPORT = OUT / "reports" / f"{SCOPE}_{SET}_render_manifest.json"
START = time.time()


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def bounds(objects):
    points = [ob.matrix_world @ Vector(p) for ob in objects for p in ob.bound_box]
    if not points:
        raise ValueError("No mesh in render selection")
    return Vector([min(p[i] for p in points) for i in range(3)]), Vector([max(p[i] for p in points) for i in range(3)])


def main():
    scene_file = OUT / "geometry" / SCOPE / f"{SCOPE}.blend"
    model = OUT / "geometry" / SCOPE / f"{SCOPE}_details.obj"
    geometry_report = OUT / "reports" / f"{SCOPE}_geometry_report.json"
    if json.loads(geometry_report.read_text())["status"] != "PASS":
        raise RuntimeError("Geometry validation must pass before rendering")
    model_hash = sha(model)
    bpy.ops.wm.open_mainfile(filepath=str(scene_file))
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 40
    scene.cycles.use_denoising = True
    device = "CPU"
    try:
        preferences = bpy.context.preferences.addons["cycles"].preferences
        preferences.compute_device_type = "OPTIX"
        preferences.get_devices()
        if any(d.type == "OPTIX" for d in preferences.devices):
            for d in preferences.devices:
                d.use = d.type == "OPTIX"
            scene.cycles.device = "GPU"
            device = "OPTIX"
    except Exception:
        pass
    scene.render.image_settings.file_format = "PNG"
    scene.render.resolution_percentage = 100
    camera = bpy.data.objects.new("ReviewCamera", bpy.data.cameras.new("ReviewCamera"))
    scene.collection.objects.link(camera)
    scene.camera = camera
    camera.data.clip_start = 2
    camera.data.clip_end = 10000000
    details = list(bpy.data.collections["Details"].objects)
    all_meshes = [ob for ob in scene.objects if ob.type == "MESH"]
    positions = {ob.name: sum(bounds([ob]), Vector((0, 0, 0))) / 2 for ob in details}
    specification = json.loads((OUT / "specs" / f"{SCOPE}_compiled.json").read_text())
    images = []
    preview = OUT / "previews" / SCOPE / SET
    preview.mkdir(parents=True, exist_ok=True)

    def save(name, target):
        path = preview / f"{name}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        images.append({"name": name, "path": str(path), "sha256": sha(path), "model_sha256": model_hash, "target": target, "camera_cm": list(camera.location), "rotation_rad": list(camera.rotation_euler), "resolution": [scene.render.resolution_x, scene.render.resolution_y]})
        REPORT.write_text(json.dumps({"status": "RENDERING", "model_sha256": model_hash, "images": images}, indent=2), encoding="utf-8")

    def framed(name, objects, azimuth, elevation, target, resolution=(1600, 1200)):
        lo, hi = bounds(objects)
        aim = (lo + hi) / 2
        radius = max((hi - lo).length * 1.5, 3000)
        a, e = math.radians(azimuth), math.radians(elevation)
        camera.location = aim + (Vector((0, 0, radius)) if elevation == 90 else Vector((radius * math.cos(a), radius * math.sin(a), radius * math.tan(e))))
        camera.rotation_euler = (aim - camera.location).to_track_quat("-Z", "Y").to_euler()
        camera.data.type = "ORTHO"
        scene.render.resolution_x, scene.render.resolution_y = resolution
        camera.data.ortho_scale = max((hi - lo).length * .9, 300) * (1.15 if elevation == 90 else 1.35)
        save(name, target)

    def local_world(building, x, y, z):
        cfg = json.loads((ROOT / "run_config.json").read_text())
        import importlib.util
        module_path = (ROOT / cfg["inputs"]["map_common"]).resolve()
        module = importlib.util.spec_from_file_location("map_common", module_path)
        mc = importlib.util.module_from_spec(module)
        module.loader.exec_module(mc)
        h, w = specification["label_shape"]
        angle = math.radians(-building["angle_image_deg"])
        ox = mc.ue_x_of_col(building["origin_px1"][0] * mc.SCALE, w)
        oy = mc.ue_y_obj_of_row(building["origin_px1"][1] * mc.SCALE, h)
        return Vector((ox + 100 * (math.cos(angle) * x - math.sin(angle) * y), oy + 100 * (math.sin(angle) * x + math.cos(angle) * y), 100 * z))

    def facade(name, building):
        x0, y0, x1, _ = building["bounds_local_m"]
        x = (x0 + x1) / 2
        floor = building["floor_m"]
        camera.location = local_world(building, x, y0 - 5.5, floor + 1.65)
        aim = local_world(building, x, y0, floor + 2.05)
        camera.rotation_euler = (aim - camera.location).to_track_quat("-Z", "Y").to_euler()
        camera.data.type = "PERSP"
        camera.data.lens = 18
        scene.render.resolution_x, scene.render.resolution_y = 1600, 1100
        save(name, building["id"])

    def label_overlay():
        import importlib.util
        cfg = json.loads((ROOT / "run_config.json").read_text())
        module = importlib.util.spec_from_file_location("map_common", (ROOT / cfg["inputs"]["map_common"]).resolve())
        mc = importlib.util.module_from_spec(module)
        module.loader.exec_module(mc)
        allowed = json.loads((OUT / "audit/label40.geojson").read_text())
        polygons = allowed["coordinates"] if allowed["type"] == "MultiPolygon" else [allowed["coordinates"]]
        collection = bpy.data.collections.new("Review_Label_Overlay")
        scene.collection.children.link(collection)
        material = bpy.data.materials.new("Review_Label_Boundary")
        material.use_nodes = True
        material.node_tree.nodes.clear()
        output = material.node_tree.nodes.new("ShaderNodeOutputMaterial")
        emission = material.node_tree.nodes.new("ShaderNodeEmission")
        emission.inputs["Color"].default_value = (.04, .8, .1, 1)
        material.node_tree.links.new(emission.outputs[0], output.inputs[0])
        z = bounds(all_meshes)[1][2] + 100
        h, w = specification["label_shape"]
        for index, polygon in enumerate(polygons):
            curve = bpy.data.curves.new(f"Boundary_{index}", "CURVE")
            curve.dimensions = "3D"
            curve.bevel_depth = 7
            for ring in polygon:
                spline = curve.splines.new("POLY")
                spline.points.add(len(ring) - 1)
                for point, (x, y) in zip(spline.points, ring):
                    point.co = (mc.ue_x_of_col(x * mc.SCALE, w), mc.ue_y_obj_of_row(y * mc.SCALE, h), z, 1)
            obj = bpy.data.objects.new(curve.name, curve)
            collection.objects.link(obj)
            curve.materials.append(material)
        framed("F1L_label_overlay", all_meshes, 0, 90, "whole_map_label_boundary", (1900, 1900))
        collection.hide_render = True

    houses = [b for b in specification["buildings"] if b["archetype"] != "annex_shed"]
    if not houses:
        raise ValueError("No main building available for facade review")
    if SCOPE == "representative":
        framed("S3_01_oblique", details, 215, 35, "representative_details")
        facade("S3_02_facade_5m5", houses[0])
        framed("S3_03_courtyard_top", details, 0, 90, "representative_details", (1500, 1500))
        framed("S0_context_alignment", all_meshes, 0, 90, "source_base_and_ground", (1200, 1200))
    elif SET == "fixed":
        framed("F1_top", all_meshes, 0, 90, "whole_map", (1900, 1900))
        framed("F2_oblique_NE", all_meshes, 45, 45, "whole_map")
        framed("F3_oblique_SW", all_meshes, 225, 45, "whole_map")
        lo, hi = bounds(details)
        map_center = (lo + hi) / 2
        by_center = sorted(details, key=lambda ob: (positions[ob.name] - map_center).length)
        framed("F4_central_detail", by_center[:min(80, len(by_center))], 135, 25, "central_objects")
        roofs = [ob for ob in details if ob.get("category") in {"roof", "annex_roof"}]
        largest_roof = max(roofs or details, key=lambda ob: (bounds([ob])[1] - bounds([ob])[0]).length)
        roof_context = [ob for ob in details if (positions[ob.name] - positions[largest_roof.name]).length < 2500]
        framed("F5_large_roof", roof_context or [largest_roof], 45, 45, largest_roof.name)
        facade("F6_facade_5m5", houses[0])
        gates = [ob for ob in details if "_GATE" in ob.name]
        chosen = gates[0] if gates else details[0]
        near_gate = [ob for ob in details if (positions[ob.name] - positions[chosen.name]).length < 1800]
        framed("F7_gate", near_gate or [chosen], 135, 30, chosen.name)
        label_overlay()
    else:
        rng = random.Random(20260917)
        facade("R1_random_facade", rng.choice(houses))
        for name, distance in (("R2_random_neighbors", 3000), ("R3_random_gate", 1800), ("R4_random_edge", 2200)):
            pool = [ob for ob in details if "_GATE" in ob.name] if "gate" in name else details
            picked = rng.choice(pool or details)
            near = [ob for ob in details if (positions[ob.name] - positions[picked.name]).length < distance]
            framed(name, near or [picked], rng.uniform(0, 360), rng.uniform(15, 65), picked.name)
    REPORT.write_text(json.dumps({"status": "RENDERED_PENDING_VISUAL_REVIEW", "model_sha256": model_hash, "images": images, "device": device, "elapsed_s": time.time() - START}, indent=2), encoding="utf-8")


try:
    main()
except Exception:
    error = traceback.format_exc()
    REPORT.write_text(json.dumps({"status": "FAILED", "error": error}, indent=2), encoding="utf-8")
    print(error)
