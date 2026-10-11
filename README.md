# Windows 人员状态标注工具

当前版本 **0.1.5**。面向 Windows 10/11 x64，在完整原视频中人工选择已有轨迹、标记工作/等待区间。直接原生播放视频，无需转码、换封装或裁剪；桌面工具不运行跟踪、骨架提取、状态预测或训练。

完整操作说明：[标注工具使用教程](标注工具使用教程.md)。便携包内也附带这份教程，与可执行文件放在同一目录。

## 最方便：便携版

从公开仓库的 [Releases](https://github.com/setsunaxkh/worker-annotation-desktop/releases) 下载 `WorkerAnnotator-0.1.5-windows-x64.zip`。完整解压后双击 `WorkerAnnotator.exe`，保留旁边的 `_internal` 目录。不需要安装 Python、FFmpeg 或 7-Zip。

同一发行版提供 `.zip.sha256` 校验文件，可用 PowerShell `Get-FileHash` 核对下载的 ZIP。

## 从源码启动

需要已安装的 **Python 3.12 x64** 和联网下载依赖。克隆仓库后，唯一应用入口是根目录的 **launch.cmd**。首次缺少环境时会自动执行配置，之后直接启动当前源码。

配置只创建项目内 `.venv`、下载固定且校验过的依赖，并以 editable 模式安装本仓库源码；不修改全局 Python，也不自动安装 Python。维护用配置及打包 CMD 位于 `scripts/`，普通标注只使用 `launch.cmd`。

若 Python 不在 PATH，可运行 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -Python "你的Python3.12可执行文件路径"`。没有 Python 的协作者使用便携版。

## 标注操作

1. 打开完整原视频，读取配套 `clip_meta.json`，导入 ROI JSON 和已有轨迹 JSON；继续旧标注时再导入已有标签。
2. 播放或跳转到所需时间，点击视频目标框或左侧目标列表选择人员。
3. 按 **I** 设置开始，按 **O** 设置结束，选择状态并点击 **添加区间**。
4. 检查右侧区间表格，导出到新的 `.json` 文件。原视频和导入原件保持只读，已有兼容字段和人工区间继续保留。

左右方向键跳转前后 1 秒，Shift＋左右方向键跳转前后 5 秒；完整快捷键与状态判断规则见教程。菜单、序号列和表格角落采用一致的浅色显示。

ROI 沿用原工具的 `area_roi`、`camera_id`、`area_id` 和可选 `core_work_zone`，蓝色显示 ROI，橙色显示核心作业区。旧标签中的 ROI 会自动显示；显示开关不影响坐标导出或人员选择。

可选骨架显示只读取轨迹文件中已有的关节点，没有关节点时只显示人员框。轨迹兼容 `stage2_pose_tracks_v1`，标签兼容 `stage2_worker_annotation_v1`。显式源时间映射沿用输入 JSON，不跨轨迹自动合并人员。

可选命令行：`launch.cmd --video "原视频路径" --roi "ROI JSON路径" --tracks "轨迹JSON路径" --labels "旧标签JSON路径" --meta "时间映射JSON路径"`。参数都可省略，再在界面选择文件。

原视频、轨迹与标签请单独分享；代码仓库与便携包不包含真实数据或模型权重。

## 开发与打包

完成配置后运行 `.venv\Scripts\python.exe -m unittest discover -s tests -v` 检查合成数据测试。源码已安装到项目 `.venv`，无需手工设置 PYTHONPATH。

运行 `scripts/build_portable.cmd` 生成 `release/` 下的便携 ZIP 和 SHA256。PyInstaller 使用 onedir，包含原生 libmpv、Qt Windows 平台插件、许可证和使用教程。

GitHub Actions 在 Windows/Python 3.12 下从 `requirements.lock.txt` 的精确轮子哈希安装并构建。版本标签 `v0.1.5` 发布便携 ZIP 和校验文件。

应用代码采用 **GPL-3.0-or-later**。播放库来自 mpv 官方安装页列出的 shinchiro 第三方构建，并非 mpv 官方二进制。固定版本、哈希、来源及源码地址见 `runtime/manifest.json` 和 `THIRD_PARTY_NOTICES.md`。
