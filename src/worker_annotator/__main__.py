from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="完整原视频人员状态标注")
    parser.add_argument("--video", type=Path)
    parser.add_argument("--tracks", type=Path)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--meta", type=Path)
    parser.add_argument("--roi", type=Path)
    args = parser.parse_args()
    from PySide6.QtCore import QStandardPaths
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication(sys.argv[:1])
    app.setApplicationName("WorkerAnnotator")
    app.setOrganizationName("WorkerAnnotator")
    app.setStyle("Fusion")
    log_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=log_dir / "app.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
    try:
        from .app import AnnotationWindow
        window = AnnotationWindow()
    except Exception as exc:
        logging.exception("Startup failed")
        QMessageBox.critical(None, "启动失败", f"{exc}\n\n请运行 scripts\\bootstrap.cmd，或使用完整的便携发行包。")
        return 1
    window.show()
    window.load_startup(args.video, args.tracks, args.labels, args.meta, args.roi)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
