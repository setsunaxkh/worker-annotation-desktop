from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QSettings, QStandardPaths, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QProgressDialog, QPushButton, QSlider, QSplitter, QTableWidget, QTableWidgetItem,
    QToolBar, QVBoxLayout, QWidget,
)

from . import __version__
from .data import AnnotationDocument, STATE_NAMES, TimeMapping, TrackIndex, format_time, load_json
from .player import NativePlayer, VideoWidget


class _Job(QObject):
    result = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, fn: Callable, callback: Callable):
        super().__init__()
        self.fn = fn
        self.callback = callback

    def run(self):
        try:
            self.result.emit((self.callback, self.fn()))
        except Exception as exc:
            logging.exception("Input import failed")
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


def seconds_box() -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(0, 999999)
    box.setDecimals(3)
    box.setSingleStep(0.1)
    box.setSuffix(" 秒")
    box.setKeyboardTracking(False)
    return box


class SegmentDialog(QDialog):
    def __init__(self, segment: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑人工区间")
        self.resize(400, 240)
        self.person = QLineEdit(str(segment.get("tracklet_id", "")))
        self.person.setReadOnly(True)
        self.start, self.end = seconds_box(), seconds_box()
        self.start.setValue(float(segment["start_time"]))
        self.end.setValue(float(segment["end_time"]))
        self.state = QComboBox()
        for code, name in STATE_NAMES.items():
            self.state.addItem(name, code)
        code = segment.get("worker_state", "unknown")
        if self.state.findData(code) < 0:
            self.state.addItem(str(code), code)
        self.state.setCurrentIndex(self.state.findData(code))
        self.note = QLineEdit(str(segment.get("note", "")))
        form = QFormLayout(self)
        form.addRow("目标", self.person)
        form.addRow("开始", self.start)
        form.addRow("结束", self.end)
        form.addRow("状态", self.state)
        form.addRow("备注", self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


class AnnotationWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1500, 930)
        self.setMinimumSize(1050, 650)
        self.settings = QSettings()
        self.video_path: Path | None = None
        self.tracks: TrackIndex | None = None
        self.document = AnnotationDocument()
        self.selected_id: str | None = None
        self.visible: list[dict] = []
        self._jobs: list = []
        self._last_list_bucket = -1
        self._recovery_path: Path | None = None
        self._draft_id: str | None = None
        self._startup_inputs = None
        self._meta_expected_sha = None
        self._loaded = False
        self._build_ui()
        self.player = NativePlayer(self.video, self)
        self.player.position_changed.connect(self.on_position)
        self.player.duration_changed.connect(self.on_duration)
        self.player.media_loaded.connect(self.on_media_loaded)
        self.player.paused_changed.connect(lambda paused: self.play_button.setText("播放  [Space]" if paused else "暂停  [Space]"))
        self.player.error.connect(self.show_error)
        self.video.clicked.connect(self.select_from_video)
        self.video.resized.connect(self.refresh_overlay)
        self._install_shortcuts()
        self.autosave = QTimer(self)
        self.autosave.setInterval(10000)
        self.autosave.timeout.connect(self.save_recovery)
        self.autosave.start()
        self.update_title()

    def _button(self, title, callback):
        button = QPushButton(title)
        button.clicked.connect(callback)
        return button

    def _build_ui(self):
        self.setStyleSheet("""
            QWidget { color: #1f2937; }
            QMainWindow, QDialog { background: #f5f7fa; }
            QGroupBox { font-weight: 600; border: 1px solid #d5dce5; border-radius: 6px; margin-top: 12px; padding: 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QPushButton { padding: 6px 10px; background: #ffffff; border: 1px solid #bcc9d9; border-radius: 4px; }
            QPushButton:hover { background: #e7f0ff; }
            QToolBar { spacing: 8px; padding: 6px; background: #ffffff; border-bottom: 1px solid #d5dce5; }
            QToolButton { padding: 5px 8px; background: #ffffff; }
            QToolButton:hover { background: #e7f0ff; }
            QLineEdit, QDoubleSpinBox, QComboBox { padding: 4px; background: #ffffff; border: 1px solid #bcc9d9; border-radius: 3px; }
            QListWidget, QTableWidget { background: #ffffff; gridline-color: #e0e6ee; selection-background-color: #dceaff; selection-color: #102b50; }
            QHeaderView::section { background: #edf2f8; color: #1f2937; padding: 5px; border: 1px solid #d5dce5; }
            QCheckBox::indicator { width: 14px; height: 14px; background: #ffffff; border: 1px solid #8395ac; border-radius: 2px; }
            QCheckBox::indicator:checked { background: #2563eb; border: 1px solid #2563eb; }
        """)
        bar = QToolBar("输入与保存", self)
        bar.setMovable(False)
        self.addToolBar(bar)
        for name, fn in [("打开原视频", self.choose_video), ("导入轨迹 JSON", self.choose_tracks),
                         ("导入已有标签", self.choose_labels), ("时间对齐", self.configure_mapping),
                         ("导出标签 JSON", self.export_labels), ("操作说明", self.show_help)]:
            action = QAction(name, self)
            action.triggered.connect(fn)
            bar.addAction(action)
        self.source_label = QLabel("先打开完整视频，再导入已有轨迹。")
        self.source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.source_label.setWordWrap(True)
        self.input_label = QLabel("未导入轨迹")
        self.input_label.setWordWrap(True)

        left = QWidget()
        left.setMinimumWidth(180)
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("选择目标"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索轨迹 ID")
        self.search.textChanged.connect(self.refresh_target_list)
        left_layout.addWidget(self.search)
        self.only_visible = QCheckBox("仅显示当前画面的目标")
        self.only_visible.toggled.connect(self.refresh_target_list)
        left_layout.addWidget(self.only_visible)
        self.target_list = QListWidget()
        self.target_list.currentItemChanged.connect(self.on_target_selected)
        self.target_list.itemDoubleClicked.connect(lambda item: self.seek_target_start())
        left_layout.addWidget(self.target_list, 1)
        left_layout.addWidget(self._button("跳到所选目标首次出现", self.seek_target_start))
        left_layout.addWidget(self.input_label)
        self.boxes_check = QCheckBox("显示目标框")
        self.boxes_check.setChecked(True)
        self.boxes_check.toggled.connect(self.refresh_overlay)
        left_layout.addWidget(self.boxes_check)
        self.skeleton_check = QCheckBox("显示已有骨架（如有）")
        self.skeleton_check.toggled.connect(self.refresh_overlay)
        left_layout.addWidget(self.skeleton_check)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.addWidget(self.source_label)
        self.video = VideoWidget()
        self.video.setMinimumSize(480, 270)
        center_layout.addWidget(self.video, 1)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderReleased.connect(lambda: self.player.seek(self.slider.value() / 1000))
        center_layout.addWidget(self.slider)
        controls = QHBoxLayout()
        self.play_button = self._button("播放  [Space]", lambda: self.player.toggle_pause())
        controls.addWidget(self.play_button)
        controls.addWidget(self._button("−5 秒", lambda: self.player.seek_relative(-5)))
        controls.addWidget(self._button("+5 秒", lambda: self.player.seek_relative(5)))
        controls.addWidget(self._button("前一帧", lambda: self.player.step_frame(True)))
        controls.addWidget(self._button("后一帧", lambda: self.player.step_frame(False)))
        self.speed = QComboBox()
        for value in [0.25, 0.5, 1, 1.5, 2, 4]:
            self.speed.addItem(f"{value}×", value)
        self.speed.setCurrentIndex(2)
        self.speed.currentIndexChanged.connect(lambda: self.player.set_speed(self.speed.currentData()))
        controls.addWidget(self.speed)
        center_layout.addLayout(controls)
        timeline = QHBoxLayout()
        self.clock_label = QLabel("00:00:00.000 / 00:00:00.000")
        timeline.addWidget(self.clock_label, 1)
        self.goto_time = seconds_box()
        timeline.addWidget(self.goto_time)
        timeline.addWidget(self._button("跳转", lambda: self.player.seek(self.goto_time.value())))
        center_layout.addLayout(timeline)

        right = QWidget()
        right.setMinimumWidth(320)
        right_layout = QVBoxLayout(right)
        group = QGroupBox("划定人工状态区间")
        form = QFormLayout(group)
        self.target_label = QLabel("请在画面点击目标框，或从左侧选择。")
        self.target_label.setWordWrap(True)
        form.addRow("目标", self.target_label)
        self.start_box, self.end_box = seconds_box(), seconds_box()
        start_row = QHBoxLayout()
        start_row.addWidget(self.start_box)
        start_row.addWidget(self._button("取当前  [I]", self.mark_start))
        form.addRow("开始", start_row)
        end_row = QHBoxLayout()
        end_row.addWidget(self.end_box)
        end_row.addWidget(self._button("取当前  [O]", self.mark_end))
        form.addRow("结束", end_row)
        self.state_combo = QComboBox()
        self.state_combo.addItem("请选择状态", None)
        for code, name in STATE_NAMES.items():
            self.state_combo.addItem(name, code)
        form.addRow("状态", self.state_combo)
        self.note = QLineEdit()
        self.note.setPlaceholderText("可备注遮挡或串人；按实际区间标记")
        form.addRow("备注", self.note)
        self.add_button = self._button("添加区间  [Enter]", self.add_segment)
        form.addRow(self.add_button)
        right_layout.addWidget(group)
        self.label_summary = QLabel("0 条人工区间")
        right_layout.addWidget(self.label_summary)
        self.filter_labels = QCheckBox("只看所选目标的区间")
        self.filter_labels.toggled.connect(self.refresh_segments)
        right_layout.addWidget(self.filter_labels)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["目标 / 状态", "开始", "结束", "备注"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemDoubleClicked.connect(self.jump_segment)
        right_layout.addWidget(self.table, 1)
        edit_row = QHBoxLayout()
        edit_row.addWidget(self._button("编辑所选区间", self.edit_segment))
        edit_row.addWidget(self._button("删除所选区间", self.delete_segment))
        right_layout.addLayout(edit_row)
        self.issues_label = QLabel("")
        self.issues_label.setWordWrap(True)
        right_layout.addWidget(self.issues_label)
        right_layout.addWidget(self._button("导出标签 JSON  [Ctrl+S]", self.export_labels))

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(center)
        splitter.addWidget(right)
        splitter.setSizes([220, 850, 430])
        self.setCentralWidget(splitter)
        self.statusBar().showMessage("原件直接播放 · 人工选人划区间 · 标注导出为独立 JSON")

    def _install_shortcuts(self):
        for key, fn in [("Space", self.player.toggle_pause), ("I", self.mark_start), ("O", self.mark_end),
                        ("Return", self.add_segment), ("Left", lambda: self.player.seek_relative(-5)),
                        ("Right", lambda: self.player.seek_relative(5)),
                        (",", lambda: self.player.step_frame(True)), (".", lambda: self.player.step_frame(False)),
                        ("Ctrl+S", self.export_labels)]:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(lambda callback=fn, hotkey=key: self._shortcut(callback, hotkey))

    def _shortcut(self, callback, key):
        focus = QApplication.focusWidget()
        if key != "Ctrl+S" and isinstance(focus, (QLineEdit, QDoubleSpinBox, QComboBox)):
            return
        callback()

    def update_title(self):
        name = self.video_path.name if self.video_path else "完整视频人工标注"
        self.setWindowTitle(f"{name}{' *' if self.document.dirty else ''} — Worker Annotator {__version__}")

    @Slot(str)
    def show_error(self, message):
        self.statusBar().showMessage(str(message))
        QMessageBox.warning(self, "操作未完成", str(message))

    def _pick(self, title, pattern):
        path, _ = QFileDialog.getOpenFileName(self, title, self.settings.value("input_dir", ""), pattern)
        if path:
            self.settings.setValue("input_dir", str(Path(path).parent))
            return Path(path)
        return None

    def _may_replace(self):
        if not self.document.dirty:
            return True
        answer = QMessageBox.question(self, "保存当前人工标注", "当前标注尚未导出。切换输入前是否导出？",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Save:
            return self.export_labels()
        if answer == QMessageBox.StandardButton.Discard:
            self.save_recovery()
            return True
        return False

    def choose_video(self):
        path = self._pick("打开完整原视频", "视频 (*.mp4 *.mkv *.avi *.mov *.mpeg *.mpg *.ts *.m2ts *.dav);;所有文件 (*)")
        if path:
            self.open_video(path)

    def open_video(self, path: Path):
        path = Path(path).resolve()
        if not path.is_file():
            self.show_error(f"找不到视频：{path}")
            return False
        if not self._may_replace():
            return False
        self.save_recovery()
        self._recovery_path = None
        self.video_path = path
        self.document = AnnotationDocument(video_name=path.name)
        self.tracks = None
        self.selected_id = None
        self._draft_id = None
        self._loaded = False
        self._meta_expected_sha = None
        self.target_list.clear()
        self.input_label.setText("未导入轨迹：可播放；导入后可选目标。")
        self.target_label.setText("请导入对应轨迹 JSON。")
        self.source_label.setText(f"正在打开原件：{path.name}")
        self.source_label.setToolTip(str(path))
        self.refresh_segments()
        self.player.set_overlay([])
        self.player.load(str(path))
        self.update_title()
        return True

    def on_media_loaded(self, info):
        self._loaded = True
        width, height = int(info.get("width", 0)), int(info.get("height", 0))
        self.video.source_dimensions(width, height)
        self.document.metadata["video_width"] = width
        self.document.metadata["video_height"] = height
        self.source_label.setText(f"{self.video_path.name if self.video_path else ''}  ·  {width}×{height}  ·  {info.get('codec', '')}  ·  原件播放")
        self.on_duration(float(info.get("duration") or self.player.duration or 0))
        if self._startup_inputs:
            tracks, labels, meta = self._startup_inputs
            self._startup_inputs = None
            try:
                if meta:
                    self.apply_meta(Path(meta))
            except Exception as exc:
                self.show_error(str(exc))
                return
            if tracks:
                self.import_tracks(Path(tracks), after=lambda: self.import_labels(Path(labels)) if labels else None)
            elif labels:
                self.import_labels(Path(labels))

    def load_startup(self, video=None, tracks=None, labels=None, meta=None):
        if video:
            self._startup_inputs = (tracks, labels, meta)
            QTimer.singleShot(0, lambda: self.open_video(Path(video)))

    def on_duration(self, seconds):
        if seconds and seconds > 0:
            self.slider.setMaximum(int(seconds * 1000))
            self.goto_time.setMaximum(seconds)
            self.start_box.setMaximum(seconds)
            self.end_box.setMaximum(seconds)

    def on_position(self, seconds):
        seconds = max(0, float(seconds or 0))
        if not self.slider.isSliderDown():
            self.slider.setValue(int(seconds * 1000))
        self.clock_label.setText(f"{format_time(seconds)} / {format_time(self.player.duration or 0)}")
        self.visible = self.tracks.visible_at(seconds) if self.tracks else []
        self.refresh_overlay()
        bucket = int(seconds * 2)
        if self.only_visible.isChecked() and bucket != self._last_list_bucket:
            self._last_list_bucket = bucket
            self.refresh_target_list()

    def _run_job(self, message, fn, callback):
        self.player.set_pause(True)
        progress = QProgressDialog(message, "", 0, 0, self)
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        thread = QThread(self)
        job = _Job(fn, callback)
        job.moveToThread(thread)
        thread.started.connect(job.run)
        job.result.connect(self._job_completed, Qt.ConnectionType.QueuedConnection)
        job.failed.connect(self.show_error, Qt.ConnectionType.QueuedConnection)
        job.finished.connect(progress.close, Qt.ConnectionType.QueuedConnection)
        job.finished.connect(thread.quit)
        job.finished.connect(job.deleteLater)
        thread.finished.connect(thread.deleteLater)
        pair = (thread, job, progress)
        self._jobs.append(pair)
        thread.finished.connect(lambda: self._jobs.remove(pair) if pair in self._jobs else None)
        thread.start()

    @Slot(object)
    def _job_completed(self, payload):
        callback, value = payload
        try:
            callback(value)
        except Exception as exc:
            logging.exception("Import result integration failed")
            self.show_error(str(exc))

    def choose_tracks(self):
        if not self.video_path:
            self.show_error("请先打开对应的完整视频。")
            return
        path = self._pick("导入对应轨迹 JSON", "JSON (*.json)")
        if path:
            self.import_tracks(path)

    def import_tracks(self, path, after=None):
        if self.document.segments and self.tracks:
            self.show_error("当前已有标签与轨迹绑定；请先导出，再重新打开视频切换轨迹。")
            return
        mapping = self.document.mapping
        def done(index):
            try:
                if self._meta_expected_sha and self._meta_expected_sha != index.sha256:
                    raise ValueError("轨迹 SHA-256 与对齐元数据不同，请选择原轨迹文件。")
                width, height = self.document.metadata.get("video_width", 0), self.document.metadata.get("video_height", 0)
                if width and height and index.width and index.height and (width, height) != (index.width, index.height):
                    raise ValueError("轨迹坐标尺寸与当前视频不同，请选择对应视频和轨迹。")
                saved = self.document.metadata.get("annotation_context", {}).get("pose_tracks_file", {}).get("sha256")
                if saved and saved != index.sha256:
                    raise ValueError("已有标签绑定的轨迹 SHA-256 不同，不能套用同名 ID。")
                self.tracks = index
                self.input_label.setText(f"{len(index.ids)} 个已有目标\n{Path(path).name}")
                self.refresh_target_list()
                self.on_position(self.player.position)
                self._set_recovery_path()
                if after is None:
                    self._offer_recovery()
                self.statusBar().showMessage("轨迹已导入。点击画面目标框或从左侧选择人员。")
                if after:
                    after()
            except Exception as exc:
                self.show_error(str(exc))
        self._run_job("正在读取已有轨迹（不运行检测）…", lambda: TrackIndex.from_file(path, mapping), done)

    def choose_labels(self):
        if not self.video_path:
            self.show_error("请先打开对应视频。")
            return
        path = self._pick("导入已有人工标签（原文件只读）", "JSON (*.json)")
        if path:
            self.import_labels(path)

    def import_labels(self, path):
        if not self._may_replace():
            return
        fresh = AnnotationDocument()
        def read():
            fresh.import_file(path, self.tracks)
            return fresh
        def done(document):
            actual_width = self.document.metadata.get("video_width", 0)
            actual_height = self.document.metadata.get("video_height", 0)
            saved_width = document.metadata.get("video_width", 0)
            saved_height = document.metadata.get("video_height", 0)
            if actual_width and actual_height and saved_width and saved_height and (actual_width, actual_height) != (saved_width, saved_height):
                self.show_error("人工标签的画面尺寸与当前视频不同，请选择对应视频。原标签未改。")
                return
            context = document.metadata.get("annotation_context") or {}
            saved_name = context.get("source_video_name") or document.metadata.get("video_name") or ""
            active_name = self.document.metadata.get("annotation_context", {}).get("source_video_name") or self.video_path.name
            saved_ids = re.findall(r"\d{12,}", saved_name)
            active_ids = re.findall(r"\d{12,}", active_name)
            if saved_ids and active_ids and saved_ids != active_ids:
                self.show_error("人工标签的原视频编号与当前视频不同，请选择对应输入。原标签未改。")
                return
            if self.tracks:
                self.tracks.mapping = document.mapping
            self.document = document
            self.refresh_segments()
            self.on_position(self.player.position)
            self.update_title()
            self.statusBar().showMessage(f"已复用 {len(document.segments)} 条人工标签；导出将另存，原文件保留。")
        self._run_job("正在读取已有人工标签…", read, done)

    def refresh_target_list(self, *_):
        self.target_list.blockSignals(True)
        self.target_list.clear()
        if self.tracks:
            text = self.search.text().strip().lower()
            visible_ids = {str(p.get("tracklet_id", p.get("track_id", ""))) for p in self.visible}
            for track_id in self.tracks.ids:
                if text and text not in track_id.lower():
                    continue
                if self.only_visible.isChecked() and track_id not in visible_ids:
                    continue
                item = QListWidgetItem(track_id + ("  ●" if track_id in visible_ids else ""))
                item.setData(Qt.ItemDataRole.UserRole, track_id)
                self.target_list.addItem(item)
                if track_id == self.selected_id:
                    self.target_list.setCurrentItem(item)
        self.target_list.blockSignals(False)

    def on_target_selected(self, item, _previous):
        if item:
            self.select_target(item.data(Qt.ItemDataRole.UserRole))

    def select_target(self, track_id):
        if not self.tracks or track_id not in self.tracks.tracklets:
            return
        if self.selected_id != track_id:
            self._draft_id = None
            self.start_box.setValue(self.player.position or 0)
            self.end_box.setValue(self.player.position or 0)
        self.selected_id = track_id
        self.target_label.setText(track_id)
        self.refresh_target_list()
        self.refresh_overlay()
        self.refresh_segments()

    def select_from_video(self, x, y):
        hits = [p for p in self.visible if p["bbox"][0] <= x <= p["bbox"][2] and p["bbox"][1] <= y <= p["bbox"][3]]
        if hits:
            hits.sort(key=lambda p: (p["bbox"][2] - p["bbox"][0]) * (p["bbox"][3] - p["bbox"][1]))
            ids = [p["tracklet_id"] for p in hits]
            chosen = ids[(ids.index(self.selected_id) + 1) % len(ids)] if self.selected_id in ids and len(ids) > 1 else ids[0]
            self.select_target(chosen)

    def seek_target_start(self):
        if self.selected_id and self.tracks:
            first = self.tracks.first_time(self.selected_id)
            if first is not None:
                self.player.seek(first)

    def refresh_overlay(self, *_):
        if hasattr(self, "player"):
            self.player.set_overlay(self.visible, self.selected_id,
                show_skeleton=self.skeleton_check.isChecked(), show_boxes=self.boxes_check.isChecked())

    def mark_start(self):
        if not self.selected_id:
            self.statusBar().showMessage("请先选择目标。")
            return
        self._draft_id = self.selected_id
        self.start_box.setValue(self.player.position)
        self.statusBar().showMessage(f"{self.selected_id} 开始：{format_time(self.player.position)}")

    def mark_end(self):
        if not self.selected_id:
            self.statusBar().showMessage("请先选择目标。")
            return
        self.end_box.setValue(self.player.position)
        self.statusBar().showMessage(f"结束：{format_time(self.player.position)}；选状态后添加。")

    def add_segment(self):
        if not self._loaded or not self.tracks or not self.selected_id:
            self.show_error("请打开视频、导入对应轨迹并选择目标。")
            return
        state = self.state_combo.currentData()
        if not state:
            self.show_error("请选择人工判断的状态。")
            return
        try:
            self.document.add_segment(self.selected_id, self.start_box.value(), self.end_box.value(), state, self.note.text())
            self.refresh_segments()
            self.update_title()
            self.save_recovery()
            self.start_box.setValue(self.end_box.value())
            self.note.clear()
            self.statusBar().showMessage("人工区间已添加；Ctrl+S 导出 JSON。")
        except Exception as exc:
            self.show_error(str(exc))

    def refresh_segments(self, *_):
        self.table.setRowCount(0)
        for index, segment in enumerate(self.document.segments):
            if self.filter_labels.isChecked() and segment.get("tracklet_id") != self.selected_id:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            state = str(segment.get("worker_state", "unknown"))
            values = [f"{segment.get('tracklet_id', '')}\n{STATE_NAMES.get(state, state)}",
                      self._display_time(segment.get("start_time")), self._display_time(segment.get("end_time")), str(segment.get("note", ""))]
            for col, text in enumerate(values):
                cell = QTableWidgetItem(text)
                cell.setData(Qt.ItemDataRole.UserRole, index)
                self.table.setItem(row, col, cell)
        self.label_summary.setText(f"{len(self.document.segments)} 条人工区间 · 双击可跳转")
        issues = self.document.validate(self.tracks, self.player.duration if hasattr(self, "player") and self._loaded else None)
        self.issues_label.setText(f"输入提示 {len(issues)} 项（保留原区间）" if issues else "")
        self.issues_label.setToolTip("\n".join(issues[:30]))

    @staticmethod
    def _display_time(value):
        try:
            return format_time(value)
        except (ValueError, TypeError):
            return f"原值：{value}"

    def _selected_segment_index(self):
        row = self.table.currentRow()
        return self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) if row >= 0 else None

    def jump_segment(self, item):
        index = item.data(Qt.ItemDataRole.UserRole)
        segment = self.document.segments[index]
        self.select_target(segment["tracklet_id"])
        try:
            self.player.seek(float(segment["start_time"]))
        except (KeyError, TypeError, ValueError):
            self.show_error("该原始区间时间无效；可显式编辑，原文件保留。")

    def edit_segment(self):
        index = self._selected_segment_index()
        if index is None:
            return
        try:
            dialog = SegmentDialog(self.document.segments[index], self)
        except (KeyError, TypeError, ValueError):
            self.show_error("该原始区间字段缺失或无效；原文件保留，请从有效标签继续。")
            return
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.document.update_segment(index, dialog.person.text(), dialog.start.value(), dialog.end.value(),
                                             dialog.state.currentData(), dialog.note.text())
                self.refresh_segments()
                self.update_title()
                self.save_recovery()
            except Exception as exc:
                self.show_error(str(exc))

    def delete_segment(self):
        index = self._selected_segment_index()
        if index is None:
            return
        if QMessageBox.question(self, "删除当前副本的区间", "删除所选人工区间？导入原文件保持不变。") == QMessageBox.StandardButton.Yes:
            self.document.delete_segment(index)
            self.refresh_segments()
            self.update_title()
            self.save_recovery()

    def export_labels(self):
        if not self.video_path:
            self.show_error("请先打开视频。")
            return False
        suggested = self.settings.value("output_dir", str(self.video_path.parent))
        suggested = str(Path(suggested) / f"{self.video_path.stem}_worker_segments_desktop.json")
        path, _ = QFileDialog.getSaveFileName(self, "另存人工标签（不覆盖导入原件）", suggested, "JSON (*.json)")
        if not path:
            return False
        try:
            self.document.save_as(path, self.tracks)
            self.settings.setValue("output_dir", str(Path(path).parent))
            self.update_title()
            self.statusBar().showMessage(f"已导出 {len(self.document.segments)} 条区间：{path}")
            self.save_recovery()
            return True
        except Exception as exc:
            self.show_error(str(exc))
            return False

    def configure_mapping(self):
        if not self.video_path:
            return
        if self.document.segments:
            self.show_error("当前有人工标签，请先导出；时间映射不可静默迁移。重新打开视频后设置，或导入带映射的原标签。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("时间对齐（完整原视频通常保持 0 / 1）")
        form = QFormLayout(dialog)
        source = QLineEdit(self.video_path.name)
        mapping = self.document.mapping
        fields = []
        for label, value in [("视频偏移（秒）", mapping.clip_offset), ("视频倍率", mapping.clip_scale),
                             ("轨迹偏移（秒）", mapping.pose_offset), ("轨迹倍率", mapping.pose_scale)]:
            box = QDoubleSpinBox()
            box.setRange(0.000001 if "倍率" in label else -999999, 999999)
            box.setDecimals(6)
            box.setValue(value)
            form.addRow(label, box)
            fields.append(box)
        form.insertRow(0, "原视频名称", source)
        meta_button = self._button("读取现成 clip_meta.json", lambda: self._read_meta_dialog(dialog))
        form.addRow(meta_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                new_mapping = TimeMapping(*(box.value() for box in fields))
                self._set_mapping(new_mapping, source.text())
                if self.tracks:
                    self.tracks.mapping = new_mapping
                self.on_position(self.player.position)
            except Exception as exc:
                self.show_error(str(exc))

    def _read_meta_dialog(self, dialog):
        path = self._pick("读取现成时间映射", "JSON (*.json)")
        if path:
            try:
                self.apply_meta(path)
                dialog.reject()
            except Exception as exc:
                self.show_error(str(exc))

    def apply_meta(self, path):
        meta = load_json(path)
        alignment = meta.get("annotation_tool_alignment") or {}
        required = ["clip_offset_seconds", "clip_scale", "pose_offset_seconds", "pose_scale"]
        if any(key not in alignment for key in required):
            raise ValueError("元数据缺少现成的完整时间映射。")
        new_mapping = TimeMapping(*(float(alignment[key]) for key in required))
        if self.document.segments:
            raise ValueError("已有人工标签时不能更改时间映射。")
        expected_sha = meta.get("pose_tracks_sha256")
        if self.tracks:
            if expected_sha and expected_sha != self.tracks.sha256:
                raise ValueError("时间元数据的轨迹 SHA-256 与已导入轨迹不同。")
        self._set_mapping(new_mapping, meta.get("source_video_name") or alignment.get("original_video_name") or self.video_path.name)
        self._meta_expected_sha = expected_sha
        if self.tracks:
            self.tracks.mapping = new_mapping
        self.on_position(self.player.position)
        self.statusBar().showMessage("已读取现成时间映射；未修改轨迹或标签。")

    def _set_mapping(self, mapping, source_name):
        context = copy.deepcopy(self.document.metadata.get("annotation_context") or {})
        for key, value in mapping.as_context(source_name).items():
            context[key] = {**context.get(key, {}), **value} if isinstance(value, dict) else value
        self.document.mapping = mapping
        self.document.metadata["annotation_context"] = context

    def _set_recovery_path(self):
        if not self.video_path or not self.tracks:
            return
        stat = self.video_path.stat()
        key = hashlib.sha256(f"{self.video_path}|{stat.st_size}|{stat.st_mtime_ns}|{self.tracks.sha256}".encode()).hexdigest()
        folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "drafts"
        folder.mkdir(parents=True, exist_ok=True)
        self._recovery_path = folder / f"{key}.json"

    def save_recovery(self):
        if not self._recovery_path:
            return
        try:
            if not self.document.dirty:
                self._recovery_path.unlink(missing_ok=True)
                return
            temp = self._recovery_path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.document.to_dict(self.tracks), ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, self._recovery_path)
        except Exception:
            logging.exception("Recovery draft write failed")

    def _offer_recovery(self):
        if self._startup_inputs or not self._recovery_path or not self._recovery_path.exists() or self.document.segments:
            return
        if QMessageBox.question(self, "发现本机未导出草稿", "此视频和轨迹有本机草稿，是否恢复？") == QMessageBox.StandardButton.Yes:
            try:
                self.document.import_file(self._recovery_path, self.tracks)
                self.document.dirty = True
                self.tracks.mapping = self.document.mapping
                self.refresh_segments()
                self.update_title()
            except Exception as exc:
                self.show_error(str(exc))

    def show_help(self):
        QMessageBox.information(self, "最短操作说明",
            "1. 打开完整原视频；导入该视频的 pose_tracks.json。已有标签可直接导入复用。\n"
            "2. 点击画面目标框，或从左侧选择任意轨迹；双击轨迹跳到首次出现。\n"
            "3. I 取开始，O 取结束，选状态后添加区间；串人按实际区间标记‘跟踪错误’。\n"
            "4. Ctrl+S 另存 JSON，原标签文件不覆盖。\n\n"
            "Space 播放/暂停；左右键 ±5秒；逗号/句号前后逐帧。\n"
            "草稿每10秒保存在本机应用数据目录。目标框来自已导入轨迹，骨架只显示已有可信关节。")

    def closeEvent(self, event):
        if self._jobs:
            event.ignore()
            self.statusBar().showMessage("正在读取输入，请完成后关闭。")
            return
        if not self._may_replace():
            event.ignore()
            return
        self.save_recovery()
        self.player.shutdown()
        event.accept()
