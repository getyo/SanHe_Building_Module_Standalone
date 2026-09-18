# 地图输入准备

本仓库只提交建筑模块的脚本、文档、风格参数和已批准贴图；地图影像、Label、原地形 OBJ、MTL 及参考照片是本轮任务输入，不在 Git 仓库中分发。完整上游项目见 [3DMapRebuilder](https://github.com/getyo/3DMapRebuilder)。

从同一轮 3DMapRebuilder 产物复制以下文件到本目录，保持文件名与 `run_config.json` 一致：

| 本目录目标文件 | 上游来源 |
|---|---|
| `satellite.tif` | `TestInput/SanHe/satellite.tif` |
| `labels_10x_no_boundary.tif` | `TestInput/SanHe/Output_10x/labels_10x_no_boundary.tif` |
| `terrain_building.obj`、`terrain_building.mtl` | `output/terrain_adaptive/` 中同名文件 |
| `terrain_ground.obj`、`terrain_ground.mtl` | `output/terrain_adaptive/` 中同名文件 |
| `map_common.py` | 上游项目根目录同名文件 |

风格参考图放到 `inputs/style_references/<场景名>/`，并按本轮场景核对适用性。若使用的 OBJ/MTL 引用其他贴图，也须一并提供。以上路径是上游项目默认布局；若实际输出位置不同，以对应一轮的产物和独立模块的 `run_config.json` 为准。
