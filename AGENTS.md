# Windows 桌面人员状态标注

本项目由当前用户授权独立建立，面向 Windows，发布到私有 GitHub 仓库供协作者拉取/下载。

- 直接用原视频的原生解封装与解码播放，不转换、换封装、裁剪或创建派生视频。
- 人在完整视频中自由选择已有轨迹和区间。只读导入现有 JSON，不运行跟踪、骨架、状态预测或训练。
- 兼容既有 `stage2_worker_annotation_v1` 与 `stage2_pose_tracks_v1`。保留未知字段、已有人工区间及显式时间映射；不得静默改 GT、补标签、合并串人。
- 原视频、真实轨迹、真实人工标签、个人机器路径、依赖 DLL 和构建产物不提交到 Git。测试用合成输入。
- 主控集成与验收；子进程按分配的文件负责，避免同时改同一文件。
- 执行入口、依赖与打包方案只支持 Windows x64。首版固定依赖，提供便携发行包与源码启动入口。

模块契约：

`data.py` 提供 `AnnotationDocument`, `TrackIndex`, `TimeMapping`, `load_json`, `format_time`, `STATE_NAMES`。
`player.py` 提供 `VideoWidget` (QWidget) 与 `NativePlayer` (QObject)。原生 libmpv 播放，通过 ASS 叠加 bbox/可选既有 COCO17，不依赖浏览器解码。
`app.py` 主控实现 Qt 界面；`__main__.py`/`__init__.py` 主控负责入口。
`scripts/`、`requirements*.txt`、`pyproject.toml`、README、CI、打包 spec 由发布子进程负责。
