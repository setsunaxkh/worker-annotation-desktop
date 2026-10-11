"""Read-only track import and lossless manual annotation document handling.

All interval times are playback seconds. Track samples retain source pixel
coordinates; no interpolation, joint completion, or identity merging occurs.
"""
from __future__ import annotations

from bisect import bisect_left
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
from statistics import median
import tempfile
from typing import Any

from .roi import roi_fields


STATE_NAMES = {
    "working": "工作中",
    "work_preparation": "工作准备",
    "waiting_stagnant": "有人等待/停滞",
    "walking_or_moving": "路过/移动",
    "outside_or_left": "离开/不在区域",
    "occluded": "遮挡",
    "unknown": "无法判断",
    "track_error": "轨迹错误",
}


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须是有限数值。")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} 必须是有限数值。") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} 必须是有限数值。")
    return result


def _decode_json(raw: bytes) -> dict:
    def reject_constant(value: str) -> None:
        raise ValueError(f"JSON 包含非法数值 {value}。")
    payload = json.loads(raw.decode("utf-8-sig"), parse_constant=reject_constant)
    if not isinstance(payload, dict):
        raise ValueError("JSON 顶层必须是对象。")
    return payload


def load_json(path: str | Path) -> dict:
    return _decode_json(Path(path).read_bytes())


def format_time(sec: float) -> str:
    value = _number(sec, "时间")
    if value < 0:
        raise ValueError("时间不能为负。")
    milliseconds = int(math.floor(value * 1000 + 0.5))
    seconds, ms = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{ms:03d}"


@dataclass(frozen=True)
class TimeMapping:
    clip_offset: float = 0
    clip_scale: float = 1
    pose_offset: float = 0
    pose_scale: float = 1

    def __post_init__(self) -> None:
        for name in ("clip_offset", "clip_scale", "pose_offset", "pose_scale"):
            object.__setattr__(self, name, _number(getattr(self, name), name))
        if self.clip_scale <= 0 or self.pose_scale <= 0:
            raise ValueError("时间倍率必须大于 0。")

    def playback_to_source(self, sec: float) -> float:
        return self.clip_offset + _number(sec, "播放时间") * self.clip_scale

    def pose_to_playback(self, sec: float) -> float:
        return (self.pose_offset + _number(sec, "轨迹时间") * self.pose_scale - self.clip_offset) / self.clip_scale

    def as_context(self, source_name: str = "") -> dict:
        return {
            "version": 1,
            "label_time_basis": "playback_seconds",
            "source_time_basis": "source_video_display_seconds",
            "source_video_name": source_name,
            "clip_to_source": {"offset_s": self.clip_offset, "scale": self.clip_scale},
            "pose_to_source": {"offset_s": self.pose_offset, "scale": self.pose_scale},
        }

    @classmethod
    def from_context(cls, context: dict | None) -> TimeMapping:
        if context is None:
            return cls()
        if not isinstance(context, dict):
            raise ValueError("annotation_context 必须是对象。")
        if context.get("version", 1) != 1:
            raise ValueError("不支持的时间映射版本。")
        if context.get("label_time_basis", "playback_seconds") != "playback_seconds":
            raise ValueError("不支持的标注时间基准。")
        if context.get("source_time_basis", "source_video_display_seconds") != "source_video_display_seconds":
            raise ValueError("不支持的原视频时间基准。")
        clip, pose = context.get("clip_to_source", {}), context.get("pose_to_source", {})
        if not isinstance(clip, dict) or not isinstance(pose, dict):
            raise ValueError("时间映射必须是对象。")
        return cls(clip.get("offset_s", 0), clip.get("scale", 1), pose.get("offset_s", 0), pose.get("scale", 1))


class TrackIndex:
    """Index existing samples, using nearest samples only within a short gap.

    Lookup tolerance is half the median sample interval, capped at 0.6 s in
    playback time. A single sample is visible within 0.1 s. Missing joints and
    confidence values are returned as supplied, without inventing defaults.
    """
    def __init__(self) -> None:
        self.width = self.height = 0
        self.tracklets: dict[str, dict] = {}
        self.ids: list[str] = []
        self.sha256 = ""
        self.path: Path | None = None
        self.mapping = TimeMapping()
        self.metadata: dict = {}
        self._times: dict[str, list[float]] = {}
        self._intervals: dict[str, float] = {}

    @classmethod
    def from_file(cls, path: str | Path, mapping: TimeMapping | None = None) -> TrackIndex:
        index = cls()
        index.path = Path(path).resolve()
        raw = index.path.read_bytes()
        index.sha256 = hashlib.sha256(raw).hexdigest()
        payload = _decode_json(raw)
        if payload.get("schema_version") != "stage2_pose_tracks_v1":
            raise ValueError("轨迹 schema_version 必须是 stage2_pose_tracks_v1。")
        allowed_scopes = {"current_video_current_roi_run_only", "current_video_current_run_only_anonymous"}
        if payload.get("identity_scope") not in allowed_scopes:
            raise ValueError("轨迹 identity_scope 必须限定于当前视频当前运行。")
        if not isinstance(payload.get("tracklets"), list):
            raise ValueError("轨迹 JSON 必须包含 tracklets 数组。")
        index.mapping = mapping or TimeMapping()
        index.metadata = {key: value for key, value in payload.items() if key != "tracklets"}
        index.width = int(payload.get("video_width") or payload.get("width") or 0)
        index.height = int(payload.get("video_height") or payload.get("height") or 0)
        for track in payload["tracklets"]:
            if not isinstance(track, dict):
                raise ValueError("轨迹项必须是对象。")
            track_id = str(track.get("tracklet_id", "")).strip()
            if not track_id or track_id in index.tracklets:
                raise ValueError("轨迹 ID 为空或重复；不能自动合并。")
            if not isinstance(track.get("poses"), list):
                raise ValueError(f"轨迹 {track_id} 缺少 poses 数组。")
            # Invalid observations cannot supply a box, but the original file
            # remains untouched. Keep all extra fields on valid observations.
            observations = []
            for pose in track["poses"]:
                if not isinstance(pose, dict):
                    continue
                try:
                    stamp = _number(pose.get("timestamp"), "轨迹时间")
                    box = pose.get("bbox")
                    if not isinstance(box, list) or len(box) < 4:
                        continue
                    box_values = [_number(n, "bbox") for n in box[:4]]
                    if box_values[2] <= box_values[0] or box_values[3] <= box_values[1]:
                        continue
                except ValueError:
                    continue
                observations.append((stamp, pose))
            observations.sort(key=lambda item: item[0])
            # This is an in-memory viewing index only, never an exported track.
            index.tracklets[track_id] = {**track, "tracklet_id": track_id, "poses": [p for _, p in observations]}
            times = [t for t, _ in observations]
            index._times[track_id] = times
            gaps = [b - a for a, b in zip(times, times[1:]) if b > a]
            index._intervals[track_id] = median(gaps) if gaps else 0
        index.ids = list(index.tracklets)
        return index

    def first_time(self, tracklet_id: str) -> float | None:
        times = self._times.get(str(tracklet_id), [])
        return self.mapping.pose_to_playback(times[0]) if times else None

    def last_time(self, tracklet_id: str) -> float | None:
        times = self._times.get(str(tracklet_id), [])
        return self.mapping.pose_to_playback(times[-1]) if times else None

    def visible_at(self, playback_time: float) -> list[dict]:
        source = self.mapping.playback_to_source(playback_time)
        pose_time = (source - self.mapping.pose_offset) / self.mapping.pose_scale
        ratio = self.mapping.pose_scale / self.mapping.clip_scale
        result = []
        for track_id in self.ids:
            times = self._times[track_id]
            if not times:
                continue
            cursor = bisect_left(times, pose_time)
            candidates = [i for i in (cursor - 1, cursor) if 0 <= i < len(times)]
            nearest = min(candidates, key=lambda i: abs(times[i] - pose_time))
            interval = self._intervals[track_id]
            tolerance = min(0.6, interval * ratio / 2) if interval else 0.1
            if abs(times[nearest] - pose_time) * ratio > tolerance + 1e-9:
                continue
            pose = self.tracklets[track_id]["poses"][nearest]
            result.append({"tracklet_id": track_id, "timestamp": times[nearest],
                           "bbox": deepcopy(pose["bbox"][:4]),
                           "keypoints": deepcopy(pose.get("keypoints", [])),
                           "keypoint_scores": deepcopy(pose.get("keypoint_scores", []))})
        return result


class AnnotationDocument:
    def __init__(self, video_name: str = "", width: int = 0, height: int = 0,
                 mapping: TimeMapping | None = None) -> None:
        self.mapping = mapping or TimeMapping()
        self.segments: list[dict] = []
        self.metadata: dict = {
            "schema_version": "stage2_worker_annotation_v1",
            "annotation_type": "worker_state_segments",
            "video_name": video_name,
            "video_width": width,
            "video_height": height,
            "camera_id": "", "area_id": "", "area_roi": [], "core_work_zone": [],
            "annotation_context": self.mapping.as_context(video_name),
        }
        self.dirty = False
        self.imported_path: Path | None = None
        self._protected_paths: set[Path] = set()
        self._imported_mapping: TimeMapping | None = None

    @staticmethod
    def _assert_binding(metadata: dict, tracks: TrackIndex | None) -> None:
        context = metadata.get("annotation_context") or {}
        if not isinstance(context, dict):
            raise ValueError("annotation_context 必须是对象。")
        binding = context.get("pose_tracks_file") or {}
        if not isinstance(binding, dict):
            raise ValueError("pose_tracks_file 必须是对象。")
        expected = binding.get("sha256")
        if expected and (not isinstance(expected, str) or len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected.lower())):
            raise ValueError("标注绑定的轨迹 SHA-256 无效。")
        if expected and tracks is not None and str(expected).lower() != tracks.sha256.lower():
            raise ValueError("轨迹 JSON SHA-256 与标注绑定不一致；不能交换同名 ID。")

    def import_file(self, path: str | Path, tracks: TrackIndex | None = None) -> None:
        original = Path(path).resolve()
        payload = load_json(original)
        if payload.get("schema_version") != "stage2_worker_annotation_v1":
            raise ValueError("标注 schema_version 必须是 stage2_worker_annotation_v1。")
        if payload.get("annotation_type") != "worker_state_segments":
            raise ValueError("annotation_type 必须是 worker_state_segments。")
        segments = payload.get("worker_segments")
        if not isinstance(segments, list) or any(not isinstance(s, dict) for s in segments):
            raise ValueError("worker_segments 必须是对象数组。")
        mapping = TimeMapping.from_context(payload.get("annotation_context"))
        self._assert_binding(payload, tracks)
        # Complete checks before replacing an existing in-memory document.
        self.metadata = {k: deepcopy(v) for k, v in payload.items() if k != "worker_segments"}
        self.segments = deepcopy(segments)
        self.mapping = mapping
        self._imported_mapping = mapping
        self.imported_path = original
        self._protected_paths.add(original)
        self.dirty = False

    def import_roi(self, path: str | Path) -> dict:
        original = Path(path).resolve()
        payload = load_json(original)
        fields = roi_fields(payload, self.metadata.get("video_width", 0), self.metadata.get("video_height", 0))
        changed = any(self.metadata.get(k) != v for k, v in fields.items())
        # Only region fields are applied: labels, source name and clock stay intact.
        self.metadata.update(deepcopy(fields))
        self._protected_paths.add(original)
        self.dirty = self.dirty or changed
        return payload

    def _segment_fields(self, tracklet_id: str, start: float, end: float,
                        state: str, note: str) -> dict:
        track_id = str(tracklet_id).strip()
        if not track_id:
            raise ValueError("目标轨迹 ID 不能为空。")
        a, b = _number(start, "起点"), _number(end, "终点")
        if a < 0 or b <= a:
            raise ValueError("区间须满足 0 ≤ 起点 < 终点。")
        if not isinstance(state, str) or state not in STATE_NAMES:
            raise ValueError("请选择有效的人员状态。")
        source_a, source_b = self.mapping.playback_to_source(a), self.mapping.playback_to_source(b)
        if source_a < 0 or source_b <= source_a:
            raise ValueError("映射后的原视频区间无效。")
        return {"tracklet_id": track_id, "start_time": a, "end_time": b,
                "start_time_text": format_time(a), "end_time_text": format_time(b),
                "worker_state": state, "state_name": STATE_NAMES[state], "note": note,
                "source_start_time": round(source_a, 3), "source_end_time": round(source_b, 3)}

    def add_segment(self, tracklet_id: str, start: float, end: float,
                    state: str, note: str = "") -> dict:
        segment = self._segment_fields(tracklet_id, start, end, state, note)
        if self._imported_mapping is not None and not self.segments and self.mapping != self._imported_mapping:
            # An explicitly reset empty document has no GT to migrate. Preserve
            # extension fields while adopting the new clock for new intervals.
            self.metadata["annotation_context"] = self._context_with_mapping(self.metadata.get("annotation_context"))
            self._imported_mapping = self.mapping
        segment.update(confidence=1.0, label_source="manual")
        self.segments.append(segment)
        self.dirty = True
        return segment

    def update_segment(self, index: int, tracklet_id: str, start: float, end: float,
                       state: str, note: str = "") -> None:
        replacement = deepcopy(self.segments[index])
        replacement.update(self._segment_fields(tracklet_id, start, end, state, note))
        self.segments[index] = replacement
        self.dirty = True

    def delete_segment(self, index: int) -> None:
        del self.segments[index]
        self.dirty = True

    def _context_with_mapping(self, existing: dict | None) -> dict:
        context = deepcopy(existing or {})
        current = self.mapping.as_context(context.get("source_video_name", self.metadata.get("video_name", "")))
        for key, value in current.items():
            if isinstance(value, dict):
                context[key] = {**context.get(key, {}), **value}
            else:
                context[key] = value
        return context

    def to_dict(self, tracks: TrackIndex | None = None) -> dict:
        self._assert_binding(self.metadata, tracks)
        if self._imported_mapping is not None and self.segments and self.mapping != self._imported_mapping:
            raise ValueError("已有导入标注时不能改变时间映射；请先另存并创建新文档。")
        if self._imported_mapping is not None and self.segments and TimeMapping.from_context(self.metadata.get("annotation_context")) != self.mapping:
            raise ValueError("元数据中的时间映射与当前标注映射不一致。")
        result = deepcopy(self.metadata)
        context = result.get("annotation_context")
        # Keep an imported context byte-for-byte in meaning unless an explicit
        # new document mapping is selected; preserve nested extension fields.
        if self._imported_mapping is None or (not self.segments and self.mapping != self._imported_mapping):
            context = self._context_with_mapping(context)
            result["annotation_context"] = context
        if tracks is not None:
            context = result.setdefault("annotation_context", self.mapping.as_context(result.get("video_name", "")))
            # Preserve an existing binding, including its unknown metadata.
            if not context.get("pose_tracks_file"):
                context["pose_tracks_file"] = {"name": tracks.path.name if tracks.path else "", "sha256": tracks.sha256}
        result["worker_segments"] = deepcopy(self.segments)
        return result

    def save_as(self, path: str | Path, tracks: TrackIndex | None = None) -> None:
        destination = Path(path).resolve()
        for original in self._protected_paths:
            if destination == original or (destination.exists() and original.exists() and os.path.samefile(destination, original)):
                raise ValueError("不能覆盖导入的原始 JSON；请选择新的文件名。")
        if tracks is not None and tracks.path is not None and (destination == tracks.path or (destination.exists() and os.path.samefile(destination, tracks.path))):
            raise ValueError("不能覆盖只读轨迹文件。")
        payload = self.to_dict(tracks)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                             prefix=f".{destination.name}.", suffix=".tmp", delete=False) as handle:
                temporary = handle.name
                json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            temporary = None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
        self.dirty = False

    def validate(self, tracks: TrackIndex | None = None, duration: float | None = None) -> list[str]:
        issues: list[str] = []
        try:
            self._assert_binding(self.metadata, tracks)
        except ValueError as exc:
            issues.append(str(exc))
        if self._imported_mapping is not None and self.segments and self.mapping != self._imported_mapping:
            issues.append("已有导入标注的时间映射被更改。")
        try:
            if self._imported_mapping is not None and self.segments and TimeMapping.from_context(self.metadata.get("annotation_context")) != self.mapping:
                issues.append("元数据中的时间映射与当前标注映射不一致。")
        except ValueError as exc:
            issues.append(str(exc))
        limit = None
        if duration is not None:
            try:
                limit = _number(duration, "视频时长")
                if limit < 0:
                    issues.append("视频时长不能为负。")
            except ValueError as exc:
                issues.append(str(exc))
        intervals: dict[str, list[tuple[float, float, int]]] = {}
        for i, segment in enumerate(self.segments):
            prefix = f"第 {i + 1} 段："
            track_id = str(segment.get("tracklet_id", "")).strip()
            if not track_id:
                issues.append(prefix + "轨迹 ID 为空。")
            if tracks is not None and (track_id not in tracks.tracklets or not tracks.tracklets[track_id]["poses"]):
                issues.append(prefix + "当前轨迹文件没有有效目标 " + track_id + "。")
            state = segment.get("worker_state")
            if not isinstance(state, str) or state not in STATE_NAMES:
                issues.append(prefix + "状态代码未知（原标签保留）。")
            try:
                a, b = _number(segment.get("start_time"), "起点"), _number(segment.get("end_time"), "终点")
                if a < 0 or b <= a:
                    issues.append(prefix + "区间须满足 0 ≤ 起点 < 终点。")
                    continue
                if limit is not None and b > limit + 1e-6:
                    issues.append(prefix + "超出视频时长；原区间未截断。")
                source_a, source_b = self.mapping.playback_to_source(a), self.mapping.playback_to_source(b)
                if source_a < 0 or source_b <= source_a:
                    issues.append(prefix + "映射后的原视频区间无效。")
                for field, expected in (("source_start_time", source_a), ("source_end_time", source_b)):
                    if field in segment and abs(_number(segment[field], field) - expected) > 0.0011:
                        issues.append(prefix + field + " 与显式时间映射不一致；原值保留。")
                intervals.setdefault(track_id, []).append((a, b, i))
            except ValueError as exc:
                issues.append(prefix + str(exc))
        for track_id, spans in intervals.items():
            spans.sort()
            active: list[tuple[float, float, int]] = []
            for span in spans:
                active = [prior for prior in active if prior[1] > span[0] + 1e-9]
                for prior in active:
                    overlap = min(prior[1], span[1]) - span[0]
                    issues.append(f"目标 {track_id} 的第 {prior[2] + 1} / {span[2] + 1} 段重叠 {overlap:.3f} 秒；原区间保留。")
                active.append(span)
        return issues
