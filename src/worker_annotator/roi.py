"""Existing ROI JSON is read in original video pixels, without cropping."""
from __future__ import annotations

import math


def polygon(value, name="ROI", width=0, height=0, allow_empty=False):
    if allow_empty and value == []:
        return []
    if not isinstance(value, list) or len(value) < 3:
        raise ValueError(f"{name} 至少需要三个坐标点。")
    points = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError(f"{name} 坐标须为 [x, y]。")
        if any(isinstance(v, bool) for v in point):
            raise ValueError(f"{name} 坐标须为有限数值。")
        try:
            x, y = map(float, point)
        except (ValueError, TypeError):
            raise ValueError(f"{name} 坐标须为有限数值。") from None
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError(f"{name} 坐标须为有限数值。")
        if x < 0 or y < 0 or (width and x > width) or (height and y > height):
            raise ValueError(f"{name} 坐标超出当前原视频尺寸，不能自动缩放。")
        # Preserve valid input coordinates, including subpixel precision.
        points.append([int(x) if x.is_integer() else x, int(y) if y.is_integer() else y])
    if len({(float(x), float(y)) for x, y in points}) < 3:
        raise ValueError(f"{name} 至少需要三个不同坐标点。")
    area = sum(float(a[0]) * float(b[1]) - float(b[0]) * float(a[1])
               for a, b in zip(points, points[1:] + points[:1]))
    if abs(area) < 1e-9:
        raise ValueError(f"{name} 多边形面积为零。")
    return points


def roi_fields(payload, width=0, height=0):
    if not isinstance(payload, dict):
        raise ValueError("ROI JSON 顶层须为对象。")
    for key, actual in (("video_width", width), ("video_height", height)):
        declared = payload.get(key, payload.get(key.removeprefix("video_")))
        if declared is not None:
            if isinstance(declared, bool):
                raise ValueError("ROI 的视频尺寸无效。")
            try:
                number = float(declared)
            except (ValueError, TypeError):
                raise ValueError("ROI 的视频尺寸无效。") from None
            if not math.isfinite(number) or number <= 0 or number != int(number):
                raise ValueError("ROI 的视频尺寸无效。")
            if actual and number != actual:
                raise ValueError("ROI JSON 的尺寸与原视频不同，请选择对应文件；不会自动缩放。")
    fields = {"area_roi": polygon(payload.get("area_roi", payload.get("area_poly")), "ROI", width, height)}
    if "core_work_zone" in payload:
        fields["core_work_zone"] = polygon(payload["core_work_zone"], "核心作业区", width, height, allow_empty=True)
    for key in ("camera_id", "area_id"):
        if key in payload:
            if not isinstance(payload[key], str):
                raise ValueError(f"{key} 须为字符串。")
            fields[key] = payload[key]
    return fields
