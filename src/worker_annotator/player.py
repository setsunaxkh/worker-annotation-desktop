"""Native libmpv playback and one shared source/letterbox geometry.

No media conversion or model inference takes place here. Playback positions are
mpv's rebased presentation timestamps, never frame-index / nominal-fps values.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import importlib
import locale
import math
import os
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

from PySide6.QtCore import QObject, QRectF, Qt, Signal, Slot
from PySide6.QtGui import QMouseEvent, QResizeEvent
from PySide6.QtWidgets import QWidget


COCO_EDGES = ((0, 1), (0, 2), (1, 3), (2, 4), (5, 6), (5, 7),
              (7, 9), (6, 8), (8, 10), (5, 11), (6, 12), (11, 12),
              (11, 13), (13, 15), (12, 14), (14, 16))
_DLL_HANDLES: list[Any] = []


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


@dataclass(frozen=True)
class VideoGeometry:
    source_width: float
    source_height: float
    x: float
    y: float
    width: float
    height: float

    @classmethod
    def fit(cls, source_width: float, source_height: float,
            viewport_width: float, viewport_height: float,
            display_aspect: float | None = None) -> "VideoGeometry":
        if min(source_width, source_height, viewport_width, viewport_height) <= 0:
            return cls(source_width, source_height, 0, 0, 0, 0)
        aspect = display_aspect or source_width / source_height
        width = min(viewport_width, viewport_height * aspect)
        height = width / aspect
        return cls(source_width, source_height, (viewport_width - width) / 2,
                   (viewport_height - height) / 2, width, height)

    def source_to_widget(self, x: float, y: float) -> tuple[float, float]:
        if min(self.source_width, self.source_height) <= 0:
            return 0.0, 0.0
        return (self.x + x * self.width / self.source_width,
                self.y + y * self.height / self.source_height)

    def widget_to_source(self, x: float, y: float) -> tuple[float, float] | None:
        if self.width <= 0 or self.height <= 0:
            return None
        # Black bars and the exclusive right/bottom edges are not selectable.
        if not (self.x <= x < self.x + self.width and self.y <= y < self.y + self.height):
            return None
        return ((x - self.x) * self.source_width / self.width,
                (y - self.y) * self.source_height / self.height)


class VideoWidget(QWidget):
    clicked = Signal(float, float)
    resized = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setStyleSheet("background-color: black;")
        self.setMinimumSize(320, 180)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._source_size = (0, 0)
        self._display_aspect: float | None = None
        self._osd_dimensions: dict[str, Any] | None = None

    def source_dimensions(self, width: int, height: int) -> None:
        self._source_size = (max(0, int(width)), max(0, int(height)))

    @property
    def geometry_mapping(self) -> VideoGeometry:
        sw, sh = self._source_size
        dimensions = self._osd_dimensions
        if dimensions and dimensions.get("w", 0) > 0 and dimensions.get("h", 0) > 0:
            ow, oh = dimensions["w"], dimensions["h"]
            ml, mr = dimensions.get("ml", 0), dimensions.get("mr", 0)
            mt, mb = dimensions.get("mt", 0), dimensions.get("mb", 0)
            return VideoGeometry(sw, sh, ml * self.width() / ow,
                                 mt * self.height() / oh,
                                 (ow - ml - mr) * self.width() / ow,
                                 (oh - mt - mb) * self.height() / oh)
        return VideoGeometry.fit(sw, sh, self.width(), self.height(), self._display_aspect)

    def video_rect(self) -> QRectF:
        geom = self.geometry_mapping
        return QRectF(geom.x, geom.y, geom.width, geom.height)

    def source_to_widget(self, x: float, y: float) -> tuple[float, float]:
        return self.geometry_mapping.source_to_widget(x, y)

    def widget_to_source(self, x: float, y: float) -> tuple[float, float] | None:
        return self.geometry_mapping.widget_to_source(x, y)

    def _click_at(self, x: float, y: float) -> None:
        point = self.widget_to_source(x, y)
        if point is not None:
            self.setFocus()
            self.clicked.emit(*point)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._click_at(event.position().x(), event.position().y())
            event.accept()
        else:
            super().mousePressEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        # The native VO reports fresh rounded margins asynchronously.
        self._osd_dimensions = None
        super().resizeEvent(event)
        self.resized.emit()


def _pose_id(pose: Mapping[str, Any]) -> Any:
    return pose.get("tracklet_id", pose.get("track_id", pose.get("id")))


def _escape_ass(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", " ").replace("\r", " ")


def build_ass_overlay(poses: Iterable[Mapping[str, Any]], geometry: VideoGeometry,
                      selected_id: Any = None, show_skeleton: bool = False,
                      show_boxes: bool = True) -> str:
    """Render only supplied coordinates; absent/low-confidence joints stay absent."""
    if geometry.width <= 0 or geometry.height <= 0:
        return ""
    events: list[str] = []
    for pose in poses:
        track_id = _pose_id(pose)
        selected = selected_id is not None and str(track_id) == str(selected_id)
        color = "00D7FF" if selected else "60E060"  # ASS uses BBGGRR.
        stroke = 3 if selected else 2
        drawing_tag = f"{{\\an7\\pos(0,0)\\1c&H{color}&\\bord0\\shad0\\p1}}"
        bbox = pose.get("bbox", [])
        if show_boxes and isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            numbers = [_number(v) for v in bbox[:4]]
            if all(v is not None for v in numbers):
                x1, y1, x2, y2 = numbers
                if x2 > x1 and y2 > y1:
                    left, top = geometry.source_to_widget(x1, y1)
                    right, bottom = geometry.source_to_widget(x2, y2)
                    # Four filled thin polygons avoid libass closed-path fill.
                    for ax, ay, bx, by in ((left, top, right, top + stroke),
                                           (left, bottom - stroke, right, bottom),
                                           (left, top, left + stroke, bottom),
                                           (right - stroke, top, right, bottom)):
                        events.append(drawing_tag + f"m {ax:.2f} {ay:.2f} l {bx:.2f} {ay:.2f} {bx:.2f} {by:.2f} {ax:.2f} {by:.2f}{{\\p0}}")
                    label_y = max(geometry.y, top - 21)
                    events.append(f"{{\\an7\\pos({left:.2f},{label_y:.2f})\\fs18\\1c&H{color}&\\bord1\\shad0}}ID {_escape_ass(str(track_id))}")
        if not show_skeleton:
            continue
        keypoints = pose.get("keypoints") or []
        scores = pose.get("keypoint_scores") or []
        if not isinstance(keypoints, (list, tuple)):
            continue
        if not isinstance(scores, (list, tuple)):
            scores = []
        valid: dict[int, tuple[float, float]] = {}
        for index, point in enumerate(keypoints[:17]):
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            score = _number(point[2] if len(point) >= 3 else scores[index] if index < len(scores) else None)
            x, y = _number(point[0]), _number(point[1])
            if score is not None and score >= .3 and x is not None and y is not None:
                # (0,0) is a common missing-point sentinel in imported tracks.
                if x == 0 and y == 0:
                    continue
                valid[index] = geometry.source_to_widget(x, y)
        for a, b in COCO_EDGES:
            if a not in valid or b not in valid:
                continue
            x1, y1 = valid[a]
            x2, y2 = valid[b]
            length = math.hypot(x2 - x1, y2 - y1)
            if length == 0:
                continue
            dx, dy = -(y2 - y1) * stroke / (2 * length), (x2 - x1) * stroke / (2 * length)
            events.append(drawing_tag + f"m {x1+dx:.2f} {y1+dy:.2f} l {x2+dx:.2f} {y2+dy:.2f} {x2-dx:.2f} {y2-dy:.2f} {x1-dx:.2f} {y1-dy:.2f}{{\\p0}}")
        for x, y in valid.values():
            radius = 2.5
            events.append(drawing_tag + f"m {x-radius:.2f} {y-radius:.2f} l {x+radius:.2f} {y-radius:.2f} {x+radius:.2f} {y+radius:.2f} {x-radius:.2f} {y+radius:.2f}{{\\p0}}")
    return "\n".join(events)


def _import_mpv():
    """Resolve bundled native libraries before python-mpv's lazy import."""
    candidates: list[Path] = []
    override = os.environ.get("MPV_DYLIB_PATH")
    if override:
        explicit = Path(override).expanduser().resolve()
        if not explicit.exists():
            raise RuntimeError(f"MPV_DYLIB_PATH 不存在：{explicit}")
        if explicit.is_file() and explicit.name.lower() not in ("mpv-2.dll", "libmpv-2.dll", "mpv-1.dll"):
            raise RuntimeError("MPV_DYLIB_PATH DLL 名需为 mpv-2.dll、libmpv-2.dll 或 mpv-1.dll")
        candidates.append(explicit if explicit.is_dir() else explicit.parent)
    if getattr(sys, "_MEIPASS", None):
        candidates.append(Path(sys._MEIPASS) / "runtime" / "mpv")
    candidates.extend((Path(sys.executable).resolve().parent / "runtime" / "mpv",
                       Path(__file__).resolve().parents[2] / "runtime" / "mpv"))
    for directory in reversed(candidates):
        if directory.is_dir():
            os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
            if os.name == "nt":
                _DLL_HANDLES.append(os.add_dll_directory(str(directory)))
    locale.setlocale(locale.LC_NUMERIC, "C")
    try:
        return importlib.import_module("mpv")
    except (ImportError, OSError) as exc:
        raise RuntimeError("原生播放器不可用。请安装固定依赖，并将 Windows x64 libmpv DLL 放到 runtime/mpv/；"
                           f"也可设置 MPV_DYLIB_PATH。详细原因：{exc}") from exc


class NativePlayer(QObject):
    position_changed = Signal(float)
    duration_changed = Signal(float)
    media_loaded = Signal(dict)
    error = Signal(str)
    paused_changed = Signal(bool)
    _bridge = Signal(str, object)

    def __init__(self, video_widget: VideoWidget, parent: QObject | None = None):
        super().__init__(parent)
        self.video_widget = video_widget
        self._mpv = None
        self._position = 0.0
        self._duration = 0.0
        self._paused = True
        self._path: Path | None = None
        self._loading = False
        self._seeking = False
        self._closed = False
        self._poses: list[Mapping[str, Any]] = []
        self._selected_id = None
        self._show_skeleton = False
        self._show_boxes = True
        self._diagnostics: deque[str] = deque(maxlen=8)
        self._observers: list[tuple[str, Any]] = []
        self._bridge.connect(self._receive, Qt.ConnectionType.QueuedConnection)
        video_widget.resized.connect(self._refresh_overlay)

    @property
    def position(self) -> float:
        return self._position

    @property
    def duration(self) -> float:
        return self._duration

    @property
    def paused(self) -> bool:
        return self._paused

    def _ensure_engine(self) -> bool:
        if self._closed:
            self.error.emit("播放器已关闭。")
            return False
        if self._mpv is not None:
            return True
        try:
            mpv = _import_mpv()
            self._mpv = mpv.MPV(wid=str(int(self.video_widget.winId())),
                                config=False, load_scripts=False, osc=False,
                                input_default_bindings=False, input_vo_keyboard=False,
                                input_terminal=False, terminal=False,
                                # Prefer Vulkan/native WGL over D3D11/ANGLE:
                                # some hybrid Windows GPU drivers crash during
                                # D3D11 renderer teardown after otherwise valid playback.
                                vo="gpu", gpu_api="vulkan,opengl,d3d11",
                                gpu_context="winvk,win,d3d11",
                                hwdec="auto-safe", keep_open="yes", idle=True,
                                rebase_start_time=True, pause=True, osd_level=0,
                                hr_seek="yes", hr_seek_demuxer_offset=5,
                                hr_seek_framedrop=False,
                                video_rotate="no", log_handler=self._log, loglevel="warn")
            for name in ("time-pos", "duration", "pause", "seeking", "osd-dimensions", "video-params"):
                callback = lambda prop, value: self._bridge.emit(prop, value)
                self._mpv.observe_property(name, callback)
                self._observers.append((name, callback))

            @self._mpv.event_callback("file-loaded")
            def file_loaded(_event):
                self._bridge.emit("loaded", self._metadata())

            @self._mpv.event_callback("end-file")
            def ended(event):
                if event.data.reason == mpv.MpvEventEndFile.ERROR:
                    self._bridge.emit("failed", (event.data.error, mpv.ErrorCode.human_readable(event.data.error)))

            self._mpv.register_key_binding("MBTN_LEFT", self._native_click)
            return True
        except Exception as exc:
            self.error.emit(f"初始化原生播放器失败：{exc}")
            if self._mpv is not None:
                self._mpv.terminate()
                self._mpv = None
            return False

    def _log(self, level, component, message) -> None:
        if level in ("warn", "error", "fatal"):
            self._bridge.emit("diagnostic", f"{component}: {str(message).strip()}")

    def _get(self, name: str, default=None):
        try:
            return getattr(self._mpv, name)
        except (AttributeError, RuntimeError, ValueError):
            return default

    def _metadata(self) -> dict[str, Any]:
        params = self._get("video_params", {}) or {}
        return {"path": self._get("path", str(self._path)),
                "width": params.get("w", self._get("width", 0)),
                "height": params.get("h", self._get("height", 0)),
                "display_width": params.get("dw", self._get("dwidth")),
                "display_height": params.get("dh", self._get("dheight")),
                "duration": self._get("duration", 0) or 0,
                "codec": self._get("video_format", "unknown"),
                "container": self._get("file_format", "unknown"),
                "demuxer_start_time": self._get("demuxer_start_time", 0),
                "hwdec": self._get("hwdec_current", "no"),
                "clock": "rebased presentation timestamp (mpv time-pos)",
                "position": self._get("time_pos", 0) or 0,
                "mpv_version": self._get("mpv_version", "unknown")}

    def _native_click(self, state, *_args) -> None:
        if not state.startswith("u"):
            return
        position = self._get("mouse_pos", {}) or {}
        dimensions = self._get("osd_dimensions", {}) or {}
        if position.get("hover") and dimensions.get("w", 0) > 0 and dimensions.get("h", 0) > 0:
            # No QWidget access from mpv's worker thread.
            self._bridge.emit("click", (position["x"] / dimensions["w"], position["y"] / dimensions["h"]))

    @Slot(str, object)
    def _receive(self, name: str, value: Any) -> None:
        if self._closed:
            return
        if name == "diagnostic":
            self._diagnostics.append(value)
        elif name == "click":
            self.video_widget._click_at(value[0] * self.video_widget.width(), value[1] * self.video_widget.height())
        elif name == "time-pos" and value is not None and not self._loading and not self._seeking:
            number = _number(value)
            if number is not None:
                self._position = max(0.0, number)
                self.position_changed.emit(self._position)
        elif name == "duration" and value is not None and not self._loading:
            self._duration = max(0.0, _number(value) or 0)
            self.duration_changed.emit(self._duration)
        elif name == "pause" and value is not None:
            self._paused = bool(value)
            self.paused_changed.emit(self._paused)
        elif name == "seeking" and value is not None:
            was_seeking = self._seeking
            self._seeking = bool(value)
            if was_seeking and not self._seeking and not self._loading:
                # time-pos may temporarily contain the requested seek target.
                # Publish the actual decoded position only after seek completes.
                actual = _number(self._get("time_pos"))
                if actual is not None:
                    self._position = max(0.0, actual)
                    self.position_changed.emit(self._position)
        elif name == "osd-dimensions":
            self.video_widget._osd_dimensions = value
            self._refresh_overlay()
        elif name == "video-params" and value:
            self.video_widget.source_dimensions(value.get("w", 0), value.get("h", 0))
            self.video_widget._display_aspect = value.get("aspect")
            self._refresh_overlay()
        elif name == "loaded":
            if self._path is None or os.path.normcase(os.path.abspath(value["path"])) != os.path.normcase(str(self._path)):
                return
            self._loading = False
            self._position = max(0.0, _number(value.get("position")) or 0)
            self._duration = max(0.0, _number(value.get("duration")) or 0)
            self.video_widget.source_dimensions(value.get("width", 0), value.get("height", 0))
            self.position_changed.emit(self._position)
            self.duration_changed.emit(self._duration)
            self.media_loaded.emit(value)
            self._refresh_overlay()
        elif name == "failed":
            self._loading = False
            detail = "\n".join(self._diagnostics)
            self.error.emit(f"无法解封装或解码原视频：{self._path}\nlibmpv {value[0]}：{value[1]}\n{detail}")

    def load(self, path: str | Path) -> None:
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            self.error.emit(f"视频文件不存在：{source}")
            return
        if not self._ensure_engine():
            return
        self._path = source
        self._diagnostics.clear()
        self._loading = True
        self._seeking = False
        self._position = self._duration = 0.0
        self._poses = []
        self.video_widget.source_dimensions(0, 0)
        self.video_widget._osd_dimensions = None
        self.position_changed.emit(0.0)
        self.duration_changed.emit(0.0)
        self.set_pause(True)
        self._refresh_overlay()
        try:
            # Structured node API preserves Unicode, spaces and punctuation.
            self._mpv.command("loadfile", str(source), "replace")
        except Exception as exc:
            self._loading = False
            self.error.emit(f"载入原视频失败：{exc}")

    def toggle_pause(self) -> None:
        self.set_pause(not self._paused)

    def set_pause(self, paused: bool) -> None:
        if self._mpv is None:
            return
        try:
            self._mpv.pause = bool(paused)
            self._paused = bool(paused)
            self.paused_changed.emit(self._paused)
        except Exception as exc:
            self.error.emit(f"切换暂停失败：{exc}")

    def _command(self, name: str, *args) -> None:
        if self._mpv is None:
            self._seeking = False
            return
        try:
            self._mpv.command(name, *args)
        except Exception as exc:
            self._seeking = False
            self.error.emit(f"播放命令 {name} 失败：{exc}")

    def seek(self, seconds: float) -> None:
        value = _number(seconds)
        if value is None:
            self.error.emit("跳转位置必须为有限秒数。")
            return
        value = max(0.0, value)
        if self._duration > 0:
            value = min(value, self._duration)
        self._seeking = True
        self._command("seek", value, "absolute+exact")

    def seek_relative(self, seconds: float) -> None:
        value = _number(seconds)
        if value is None:
            self.error.emit("相对跳转必须为有限秒数。")
            return
        self.seek(self._position + value)

    def step_frame(self, backward: bool = False) -> None:
        self.set_pause(True)
        if backward:
            self._seeking = True
        self._command("frame-back-step" if backward else "frame-step")

    def set_speed(self, speed: float) -> None:
        value = _number(speed)
        if value is None or not .01 <= value <= 100:
            self.error.emit("播放速度必须在 0.01 到 100 之间。")
            return
        if self._mpv is not None:
            try:
                self._mpv.speed = value
            except Exception as exc:
                self.error.emit(f"设置播放速度失败：{exc}")

    def set_overlay(self, poses, selected_id=None, show_skeleton=False, show_boxes=True) -> None:
        self._poses = list(poses)
        self._selected_id = selected_id
        self._show_skeleton = bool(show_skeleton)
        self._show_boxes = bool(show_boxes)
        self._refresh_overlay()

    @Slot()
    def _refresh_overlay(self) -> None:
        if self._mpv is None or self._closed:
            return
        data = build_ass_overlay(self._poses, self.video_widget.geometry_mapping,
                                 self._selected_id, self._show_skeleton, self._show_boxes)
        try:
            # python-mpv 1.0.8's helper has a res_Y typo. The documented named
            # node command is also immune to command-string/ASS quoting issues.
            self._mpv.command("osd-overlay", id=1, format="ass-events", data=data,
                              res_x=max(1, self.video_widget.width()),
                              res_y=max(1, self.video_widget.height()), z=1)
        except Exception as exc:
            self.error.emit(f"绘制轨迹叠加失败：{exc}")

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        engine, self._mpv = self._mpv, None
        if engine is not None:
            for name, callback in self._observers:
                engine.unobserve_property(name, callback)
            engine.terminate()
