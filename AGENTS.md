# Windows 桌面人员状态标注

本项目由当前用户授权独立建立，面向 Windows，发布到 GitHub 仓库供协作者拉取/下载。2026-10-11 用户已将仓库设为公开，后续保持公开；仍只发布代码、程序与教程。

- 直接用原视频的原生解封装与解码播放，不转换、换封装、裁剪或创建派生视频。
- 人在完整视频中自由选择已有轨迹和区间。只读导入现有 JSON，不运行跟踪、骨架、状态预测或训练。
- 用户已另行授权离线输入准备助手 `scripts/prepare_annotation_inputs.py`：仅缺轨迹的新视频才执行一次 YOLO11n 人物检测＋ByteTrack，真实 PTS、5Hz、无 ReID、骨架、状态或训练；标注 GUI 本身仍只读导入。批次先试一份，已有人工 GT 源排除，视频和 JSON 同文件夹，原件以硬链接保留原路径。
- 单份快速提取已完成；用户现在需要手动运行剩余批次的程序。批处理入口为 `scripts/prepare_annotation_batch.ps1 -Run`，默认不加 `-Run` 时仅预览计划。提供程序时不自动开始整批计算；排除已有人工标签和封存来源，跳过可用完整结果，不覆盖任何人工文件，允许安全保留中断产物后重试。
- 上述离线助手及对应测试为本机数据准备工具，保留在本机，不随 Git 源码或便携应用发布；协作者收到配套视频与 JSON 后使用标注应用。
- 兼容既有 `stage2_worker_annotation_v1` 与 `stage2_pose_tracks_v1`。保留未知字段、已有人工区间及显式时间映射；不得静默改 GT、补标签、合并串人。
- 原视频、真实轨迹、真实人工标签、个人机器路径、依赖 DLL 和构建产物不提交到 Git。测试用合成输入。
- 主控集成与验收；子进程按分配的文件负责，避免同时改同一文件。
- 应用启动统一为根目录 `launch.cmd`，不得为每次修复另建启动入口；配置及打包维护入口放在 `scripts/`。
- 执行入口、依赖与打包方案只支持 Windows x64。首版固定依赖，提供便携发行包与源码启动入口。

模块契约：

`data.py` 提供 `AnnotationDocument`, `TrackIndex`, `TimeMapping`, `load_json`, `format_time`, `STATE_NAMES`。
`player.py` 提供 `VideoWidget` (QWidget) 与 `NativePlayer` (QObject)。原生 libmpv 播放，通过 ASS 叠加 bbox/可选既有 COCO17，不依赖浏览器解码。
`app.py` 主控实现 Qt 界面；`__main__.py`/`__init__.py` 主控负责入口。
`scripts/`、`requirements*.txt`、`pyproject.toml`、README、CI、打包 spec 由发布子进程负责。
