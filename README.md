# 建筑细节独立版

这是 [3DMapRebuilder 完整地图项目](https://github.com/getyo/3DMapRebuilder) 的地形产物之后、UE 导入之前的独立建筑细节阶段。由用户在 Codex 中手动开启对话；不会由 3DMap 代码调用。执行会话按项目设计使用 GPT-6 Astra（中等推理强度），由会话负责看图规划与视觉验收。新对话先读 [完整执行管线](docs/建筑细节完整执行管线_独立版.md)、`session_prompt.md` 和 `run_config.json`。

`inputs/map/` 保存本轮上游原始输入副本；`inputs/style_references/` 按风格命名目录；`style_catalog/rural_hebei/` 保存最初成功交付的农村风格参数及贴图。包内没有旧任务、旧成品或本轮独立 Stage 3 新生成的风格参数。新对话须先看图判断风格是否适用，再选择或创建 `outputs/specs/style.json`；`outputs/specs/tasks.json` 默认每张地图重新建立。

历史建筑验收图示例见 [完整执行管线的预览小节](docs/建筑细节完整执行管线_独立版.md#历史建筑验收图示例)。这些图片来自先前交付的固定视角验收，不代表本轮独立版已完成全图验收。

从 GitHub 克隆本仓库时，`inputs/map/` 的地图影像、标签和地形 OBJ 以及 `inputs/style_references/` 的参考照片不随代码提交。运行前按 [输入准备说明](inputs/map/README.md) 从同一轮 3DMapRebuilder 结果复制地图输入，并提供适用的风格参考图；再核对 `run_config.json` 中机器相关的 Blender 可执行文件路径。本地已有输入副本不受仓库忽略规则影响。

从本目录运行：

```powershell
python -B run_building_details.py gate --scene rural_hebei
python -B run_building_details.py audit
```

`gate` 返回 `NEEDS_REFERENCE_IMAGES` 时停下并向用户索要对应风格图片。选择合适风格后，按完整执行管线的 Stage 1–8 与末尾的命令表执行。代表效果须用户确认，确认记录要绑定风格、任务和图片哈希；全图命令会检查这一关口。

当前建模器支持 `rural_house`、`annex_shed`、`factory_hall` 三种几何原型。更换颜色或贴图可以使用风格参数；需要新的城市房型时，先扩展执行器和验证，再建模。未知房型不会悄悄按农村房屋生成。
