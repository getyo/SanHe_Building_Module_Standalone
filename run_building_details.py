#!/usr/bin/env python3
"""Manual conversation entry for the standalone building-detail pipeline.

The visual planner writes style.json and tasks.json. This program executes only
deterministic stages; it never guesses scene style or silently approves renders.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "run_config.json"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def resolve(value: str) -> Path:
    p = Path(value)
    return (p if p.is_absolute() else ROOT / p).resolve()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def config() -> dict:
    cfg = read(CONFIG)
    if cfg.get("schema") != "building-details-standalone/1":
        raise ValueError("Unsupported run_config schema")
    if cfg["style"]["mode"] not in {"auto", "new", "named"}:
        raise ValueError("style.mode must be auto, new or named")
    if cfg["tasks"]["mode"] not in {"fresh", "reuse"}:
        raise ValueError("tasks.mode must be fresh or reuse")
    return cfg


def input_audit(cfg: dict) -> dict:
    required = ("satellite", "label", "base_obj", "base_mtl", "ground_obj", "ground_mtl", "map_common")
    files = {key: resolve(cfg["inputs"][key]) for key in required}
    missing = [key for key, path in files.items() if not path.is_file()]
    if missing:
        return {"status": "MISSING_INPUTS", "missing": missing}
    return {"status": "READY", "inputs": {key: {"path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path), "sha256": sha(path)} for key, path in files.items()}}


def style_gate(cfg: dict, scene: str, reject_catalog: bool = False) -> dict:
    """Expose candidates. Astra decides whether a candidate fits the images."""
    mode = cfg["style"]["mode"]
    catalog = resolve(cfg["style"]["catalog_dir"]) if cfg["style"].get("catalog_dir") else None
    library = resolve(cfg["inputs"]["style_references"])
    refs = library / scene
    images = sorted(p for p in refs.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXT) if refs.is_dir() else []
    presets = sorted((catalog / scene).glob("*.json")) if catalog and (catalog / scene).is_dir() else []
    if mode == "named":
        name = cfg["style"].get("name")
        if not name:
            raise ValueError("style.name is required for named mode")
        chosen = resolve(name)
        if not chosen.is_file():
            raise FileNotFoundError(chosen)
        return {"status": "NAMED_CANDIDATE", "candidate": str(chosen), "reference_images": len(images)}
    if mode == "auto" and presets and not reject_catalog:
        return {"status": "REVIEW_PRESETS", "candidates": [str(p) for p in presets], "reference_images": len(images), "instruction": "Compare candidate applicability with current satellite and reference images; record selection or rejection."}
    if images:
        return {"status": "CREATE_FROM_REFERENCES", "reference_dir": str(refs), "reference_images": len(images)}
    return {"status": "NEEDS_REFERENCE_IMAGES", "scene": scene, "instruction": "Stop before Stage 1. Ask user for matching style reference images."}


def ensure_style_and_tasks(cfg: dict) -> tuple[Path, Path]:
    gate_path = ROOT / "outputs/audit/style_gate.json"
    if not gate_path.is_file():
        raise RuntimeError("Run gate after inspecting the satellite image before Stage 1")
    gate = read(gate_path)
    if gate.get("status") in {"NEEDS_REFERENCE_IMAGES", "MISSING_INPUTS"}:
        raise RuntimeError("Style or map input gate is blocked; obtain matching reference images first")
    fresh = input_audit(cfg)
    if gate.get("input_hashes") != {k: v["sha256"] for k, v in fresh.get("inputs", {}).items()}:
        raise RuntimeError("Inputs changed after style gate; inspect images and run gate again")
    style = resolve(cfg["style"]["output"])
    tasks = resolve(cfg["tasks"]["output"])
    if cfg["tasks"]["mode"] == "reuse":
        reuse_value = cfg["tasks"].get("reuse_file")
        if not reuse_value:
            raise ValueError("tasks.reuse_file is required when tasks.mode=reuse")
        reused = resolve(reuse_value)
        if not reused.is_file():
            raise FileNotFoundError(reused)
        metadata = read(reused)
        report_path = ROOT / "outputs/audit/task_reuse_check.json"
        expected = gate["input_hashes"]
        supplied = metadata.get("source_input_hashes")
        mismatches = {key: {"task": (supplied or {}).get(key), "current": value} for key, value in expected.items() if (supplied or {}).get(key) != value}
        label_shape = read(ROOT / "outputs/audit/input_audit.json").get("label_shape") if (ROOT / "outputs/audit/input_audit.json").is_file() else None
        if label_shape and metadata.get("label_shape") != label_shape:
            mismatches["label_shape"] = {"task": metadata.get("label_shape"), "current": label_shape}
        status = "CHECKS_MATCH" if not mismatches and metadata.get("schema") == "building-details-highlevel/2" else "NEEDS_USER_DECISION"
        report = {"status": status, "task_file": str(reused), "task_sha256": sha(reused), "task_schema": metadata.get("schema"), "mismatches": mismatches, "instruction": "Astra must inspect satellite and task layout. On mismatch or missing provenance, stop and ask user whether to make fresh tasks or explicitly migrate the old list; never build it directly on another map."}
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if status != "CHECKS_MATCH":
            raise RuntimeError(f"Task reuse is not verified for this map; see {report_path}. Ask user before proceeding.")
        review_path = ROOT / "outputs/audit/task_reuse_review.json"
        if not review_path.is_file():
            raise RuntimeError("Astra must visually compare the old task layout with the current satellite and write task_reuse_review.json")
        review = read(review_path)
        if review.get("decision") != "same_map" or review.get("task_sha256") != sha(reused) or review.get("input_hashes") != expected or not review.get("observations"):
            raise RuntimeError("Astra's task reuse review is missing, stale, or rejects this map; ask user how to proceed")
        if tasks.is_file() and sha(tasks) != sha(reused):
            raise RuntimeError("The current task output differs from the requested reuse file; ask user which task list to use")
        if not tasks.is_file():
            tasks.parent.mkdir(parents=True, exist_ok=True)
            tasks.write_bytes(reused.read_bytes())
    if not style.is_file() or not tasks.is_file():
        raise FileNotFoundError("Astra must first write outputs/specs/style.json and tasks.json")
    data = read(tasks)
    if data.get("style_id") != read(style).get("id"):
        raise ValueError("Task style_id and style id differ")
    if data.get("schema") != "building-details-highlevel/2":
        raise ValueError("Expected building-details-highlevel/2 task schema")
    return style, tasks


def check_gate(cfg: dict, scene: str, reject_catalog: bool = False) -> dict:
    audit = input_audit(cfg)
    if audit["status"] != "READY":
        return audit
    choice = style_gate(cfg, scene, reject_catalog)
    record = {"scene": scene, **choice, "input_hashes": {k: v["sha256"] for k, v in audit["inputs"].items()}}
    destination = ROOT / "outputs/audit/style_gate.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"inputs": audit, "style": record}


def run(args: list[str]) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def blender(cfg: dict, script: str, receipt: Path, *arguments: str) -> None:
    exe = resolve(cfg["blender"])
    if not exe.is_file():
        raise FileNotFoundError(f"Blender executable missing: {exe}")
    receipt.unlink(missing_ok=True)
    log = ROOT / "outputs/reports" / (script.removesuffix(".py") + ".log")
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen([str(exe), "--background", "--factory-startup", "--python", str(ROOT / "engine" / "scripts" / script), "--", *arguments], cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
        while True:
            if receipt.is_file():
                try:
                    state = read(receipt).get("status")
                except (OSError, json.JSONDecodeError):
                    state = None
                if state in {"BLENDER_READY", "FAILED", "GEOMETRY_COMPLETE_PENDING_VALIDATION", "RENDERED_PENDING_VISUAL_REVIEW"}:
                    break
            code = process.poll()
            if code not in (None, 0):
                raise RuntimeError(f"Blender launcher exited {code}; inspect {log}")
            if time.monotonic() - started > 3600:
                raise TimeoutError(f"Blender produced no receipt within 3600 s; inspect {log}")
            time.sleep(1)


def approved(cfg: dict) -> None:
    confirmation = ROOT / "outputs/reports/style_confirmation.json"
    if not confirmation.is_file():
        raise RuntimeError("Stage 3 user confirmation is missing")
    record = read(confirmation)
    style, tasks = ensure_style_and_tasks(cfg)
    if record.get("approved") is not True or record.get("style_sha256") != sha(style) or record.get("tasks_sha256") != sha(tasks):
        raise RuntimeError("Stage 3 confirmation is absent, negative, or belongs to another style/task revision")
    manifest_path = ROOT / "outputs/reports/representative_fixed_render_manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("Stage 3 render manifest is missing")
    manifest = read(manifest_path)
    if manifest.get("status") != "RENDERED_PENDING_VISUAL_REVIEW":
        raise RuntimeError("Stage 3 render set is incomplete")
    actual = {image["name"]: image["sha256"] for image in manifest["images"]}
    if record.get("image_hashes") != actual:
        raise RuntimeError("Stage 3 approval image hashes do not match the current render set")
    if record.get("model_sha256") != manifest.get("model_sha256"):
        raise RuntimeError("Stage 3 approval belongs to another model")


def visual_review_complete() -> None:
    model = ROOT / "outputs/geometry/full/full_details.obj"
    review_path = ROOT / "outputs/reports/full_visual_review.json"
    if not review_path.is_file():
        raise RuntimeError("Astra must write full_visual_review.json after inspecting fixed and random images")
    review = read(review_path)
    if review.get("reviewer") != "Astra" or review.get("decision") != "accepted" or review.get("model_sha256") != sha(model):
        raise RuntimeError("Astra's full-map visual review is incomplete or stale")
    for name in ("fixed", "random"):
        manifest = read(ROOT / f"outputs/reports/full_{name}_render_manifest.json")
        if manifest.get("status") != "RENDERED_PENDING_VISUAL_REVIEW" or manifest.get("model_sha256") != sha(model):
            raise RuntimeError(f"Full-map {name} renders are incomplete or stale")
        if review.get("image_hashes", {}).get(name) != {image["name"]: image["sha256"] for image in manifest["images"]}:
            raise RuntimeError(f"Full-map {name} visual review hashes do not match current renders")
        observations = review.get("observations", {}).get(name, {})
        if not all(isinstance(observations.get(image["name"]), str) and observations[image["name"]].strip() for image in manifest["images"]):
            raise RuntimeError(f"Astra must record a visual observation for every {name} render")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("gate", "probe", "audit", "layout", "compile", "build", "validate", "render", "package", "status"))
    parser.add_argument("--scene", help="Style reference directory name decided after visual inspection")
    parser.add_argument("--reject-catalog", action="store_true", help="Planner found catalog presets unsuitable")
    parser.add_argument("--scope", choices=("representative", "full"), default="representative")
    parser.add_argument("--render-set", choices=("fixed", "random"), default="fixed")
    parser.add_argument("--rebuild", action="store_true", help="Explicitly regenerate the requested deterministic stage")
    ns = parser.parse_args()
    cfg = config()
    if ns.action == "gate":
        if not ns.scene:
            parser.error("--scene is required after inspecting the satellite image")
        result = check_gate(cfg, ns.scene, ns.reject_catalog)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("status") != "READY" and (result.get("status") == "MISSING_INPUTS" or result.get("style", {}).get("status") == "NEEDS_REFERENCE_IMAGES") else 0
    if ns.action == "status":
        checkpoints = {"style_gate": "outputs/audit/style_gate.json", "input_audit": "outputs/audit/input_audit.json", "layout": "outputs/audit/layout_check.json", "representative_build": "outputs/reports/representative_generation_receipt.json", "representative_geometry": "outputs/reports/representative_geometry_report.json", "representative_renders": "outputs/reports/representative_fixed_render_manifest.json", "user_confirmation": "outputs/reports/style_confirmation.json", "full_build": "outputs/reports/full_generation_receipt.json", "full_geometry": "outputs/reports/full_geometry_report.json", "fixed_renders": "outputs/reports/full_fixed_render_manifest.json", "random_renders": "outputs/reports/full_random_render_manifest.json", "model_visual_review": "outputs/reports/full_visual_review.json", "ue_package": "outputs/UE_Map/coordinate_conversion.json"}
        result = {name: (read(ROOT / path).get("status", "PRESENT") if (ROOT / path).is_file() else "PENDING") for name, path in checkpoints.items()}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if ns.action == "probe":
        receipt = ROOT / "outputs/audit/blender_probe.json"
        receipt.parent.mkdir(parents=True, exist_ok=True)
        blender(cfg, "blender_probe.py", receipt)
        result = read(receipt)
        if result.get("status") != "BLENDER_READY":
            raise RuntimeError("Blender probe did not complete")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    audit = input_audit(cfg)
    if audit["status"] != "READY":
        raise RuntimeError(json.dumps(audit))
    if ns.action == "audit":
        run([sys.executable, str(ROOT / "engine/scripts/audit_inputs.py")])
        return 0
    style, tasks = ensure_style_and_tasks(cfg)
    if ns.scope == "full":
        approved(cfg)
    job = ROOT / "outputs/specs/build_job.json"
    if ns.action == "layout":
        run([sys.executable, str(ROOT / "engine/scripts/validate_layout.py")])
    elif ns.action == "compile":
        run([sys.executable, str(ROOT / "engine/scripts/compile_representative.py"), "--scope", ns.scope])
    elif ns.action == "build":
        existing = ROOT / f"outputs/reports/{ns.scope}_generation_receipt.json"
        model = ROOT / f"outputs/geometry/{ns.scope}/{ns.scope}_details.obj"
        compiled = ROOT / f"outputs/specs/{ns.scope}_compiled.json"
        if not ns.rebuild and existing.is_file() and model.is_file() and compiled.is_file():
            old = read(existing)
            if old.get("status") == "GEOMETRY_COMPLETE_PENDING_VALIDATION" and old.get("model_sha256") == sha(model) and old.get("generation_spec_sha256") == sha(compiled) and old.get("style_sha256") == sha(style) and old.get("source_sha256") == sha(resolve(cfg["inputs"]["base_obj"])):
                print(json.dumps({"status": "REUSED_VALID_BUILD", "model_sha256": old["model_sha256"]}))
                return 0
        job.parent.mkdir(parents=True, exist_ok=True)
        job.write_text(json.dumps({"work_root": str(ROOT), "project_root": str(ROOT), "style_file": str(style), "scope": ns.scope, "inputs": {k: str(resolve(v)) for k, v in cfg["inputs"].items() if k != "style_references"}}), encoding="utf-8")
        blender(cfg, "build_representative.py", ROOT / f"outputs/reports/{ns.scope}_generation_receipt.json", str(job))
        receipt = read(ROOT / f"outputs/reports/{ns.scope}_generation_receipt.json")
        if receipt.get("status") != "GEOMETRY_COMPLETE_PENDING_VALIDATION":
            raise RuntimeError(receipt.get("error", "Blender generation failed"))
    elif ns.action == "validate":
        run([sys.executable, str(ROOT / "engine/scripts/validate_representative.py"), "--scope", ns.scope])
    elif ns.action == "render":
        manifest_path = ROOT / f"outputs/reports/{ns.scope}_{ns.render_set}_render_manifest.json"
        model = ROOT / f"outputs/geometry/{ns.scope}/{ns.scope}_details.obj"
        if not ns.rebuild and manifest_path.is_file() and model.is_file():
            previous = read(manifest_path)
            if previous.get("status") == "RENDERED_PENDING_VISUAL_REVIEW" and previous.get("model_sha256") == sha(model) and previous.get("images") and all(Path(item["path"]).is_file() and sha(Path(item["path"])) == item["sha256"] for item in previous["images"]):
                print(json.dumps({"status": "REUSED_VALID_RENDERS", "count": len(previous["images"])}))
                return 0
        blender(cfg, "render_scene.py", manifest_path, ns.scope, ns.render_set)
        manifest = read(ROOT / f"outputs/reports/{ns.scope}_{ns.render_set}_render_manifest.json")
        if manifest.get("status") != "RENDERED_PENDING_VISUAL_REVIEW":
            raise RuntimeError(manifest.get("error", "Render failed"))
    elif ns.action == "package":
        if ns.scope != "full":
            raise ValueError("UE package is only for the full map")
        visual_review_complete()
        run([sys.executable, str(ROOT / "engine/scripts/pack_ue.py")])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, FileNotFoundError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
