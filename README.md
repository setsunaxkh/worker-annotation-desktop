# Windows 人员状态标注工具

完整原视频在本机直接播放，人工选择已有轨迹并标记工作/等待区间。视频无需转码、换封装或裁剪；工具不运行跟踪、骨架提取、状态预测或训练。Windows 10/11 x64，版本 0.1.2。

## 最方便：便携版

从本私有仓库的 **Releases** 下载 `WorkerAnnotator-0.1.2-windows-x64.zip`，完整解压后双击 `WorkerAnnotator.exe`。保留旁边的 `_internal` 目录；不需要安装 Python、FFmpeg 或 7-Zip。下载旁边的 `.sha256` 可以用 PowerShell `Get-FileHash` 校验 ZIP。

## 从源码启动

需要已安装的 **Python 3.12 x64** 和联网下载依赖。`git clone` 后首次双击 `bootstrap.cmd`，成功后双击 `launch.cmd`。首次设置只创建项目内 `.venv`、下载固定且校验过的依赖，并以 editable 模式安装本仓库源码，不修改全局 Python，也不自动安装 Python。

若 Python 不在 PATH，可运行 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -Python "你的Python3.12可执行文件路径"`。没有 Python 的协作者直接使用便携版。

## 标注操作

1. 选择完整原视频，导入对应的已有轨迹 JSON；需要继续已有标注时导入标签 JSON。
2. 播放/拖动时间，点选画面目标或轨迹列表中的人员。
3. 按 **I** 设置开始，按 **O** 设置结束，选择状态，点击 **Add/添加区间**。
4. 查看列表、检查区间，导出到一个新 JSON 文件。工具保留兼容字段和已有区间；不覆盖导入原件。

可选骨架显示只读取轨迹文件中已有的关节点；没有关节点时只显示人员框，不提取骨架。

轨迹兼容 `stage2_pose_tracks_v1`，标签兼容 `stage2_worker_annotation_v1`。显式源时间映射沿用输入 JSON；目标发生变化时由人决定区间，不跨轨迹自动合并。原视频、轨迹与标签请单独分享，私有代码仓库也不收真实数据。

可选命令行入口：`launch.cmd --video "原视频路径" --tracks "轨迹JSON路径" --labels "旧标签JSON路径" --meta "时间映射JSON路径"`。所有参数都可省略，再在界面选择文件。

## 开发与打包

完成 bootstrap 后运行 `.venv\Scripts\python.exe -m unittest discover -s tests -v` 检查合成数据测试。源码已安装到项目 `.venv`，测试无需手工设置 PYTHONPATH。双击 `build_portable.cmd` 生成 `release/` 下的便携 ZIP 和 SHA256。PyInstaller 使用 onedir，包含 libmpv、Qt Windows 平台插件和许可文本。

GitHub Actions 在 Windows/Python 3.12 下从 `requirements.lock.txt` 的精确轮子哈希安装并构建。推送 `v0.1.2` 类版本标签会在私有仓库创建 Release 并附 ZIP、校验文件。只公开代码和构建产物；不上传本机视频或标注。

应用代码采用 **GPL-3.0-or-later**。播放库为 mpv 官方安装页列出的 shinchiro 第三方构建，并非 mpv 官方二进制；固定版本、哈希、来源及源码地址见 `runtime/manifest.json` 和 `THIRD_PARTY_NOTICES.md`。
