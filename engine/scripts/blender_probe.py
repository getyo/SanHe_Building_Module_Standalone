import json
from pathlib import Path
import bpy

root = Path(__file__).resolve().parents[2]
out = root / 'outputs/audit/blender_probe.json'
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({'status': 'BLENDER_READY', 'version': bpy.app.version_string, 'binary_path': bpy.app.binary_path}, indent=2), encoding='utf-8')
