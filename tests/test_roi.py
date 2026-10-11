import copy
import json
import tempfile
import unittest
from pathlib import Path

from worker_annotator.data import AnnotationDocument, TimeMapping
from worker_annotator.player import VideoGeometry, build_ass_overlay
from worker_annotator.roi import roi_fields


class RoiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.payload = {"video_name": "same_camera_previous_h264.mp4", "video_width": 1920,
                        "video_height": 1080, "camera_id": "front", "area_id": "station",
                        "area_roi": [[10, 20], [1200, 20], [1200, 900], [10, 900]],
                        "core_work_zone": [[100, 200], [800, 200], [800, 600], [100, 600]]}

    def tearDown(self):
        self.temp.cleanup()

    def write(self, payload):
        path = self.root / "roi.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_roi_updates_only_region_metadata_and_preserves_gt_and_clock(self):
        doc = AnnotationDocument("original.mp4", 1920, 1080, TimeMapping(2, 1, 0, 1))
        doc.add_segment("person-a", 1, 3, "working")
        doc.metadata["private_extension"] = {"keep": True}
        before = copy.deepcopy(doc.segments)
        mapping = doc.mapping
        path = self.write(self.payload)
        source = path.read_bytes()
        doc.import_roi(path)
        exported = doc.to_dict()
        self.assertEqual(before, exported["worker_segments"])
        self.assertEqual(doc.mapping, mapping)
        self.assertEqual(exported["video_name"], "original.mp4")
        for key in ("camera_id", "area_id", "area_roi", "core_work_zone"):
            self.assertEqual(exported[key], self.payload[key])
        self.assertEqual(exported["private_extension"], {"keep": True})
        with self.assertRaises(ValueError):
            doc.save_as(path)
        self.assertEqual(path.read_bytes(), source)

    def test_mismatch_and_bad_core_reject_without_partial_changes(self):
        doc = AnnotationDocument("original.mp4", 1920, 1080)
        before = copy.deepcopy(doc.metadata)
        for patch in ({"video_width": 1280}, {"core_work_zone": [[0, 0], [2000, 0], [0, 100]]},
                      {"area_roi": [[0, 0], [1, 1], [2, 2]]}):
            with self.assertRaises(ValueError):
                doc.import_roi(self.write({**self.payload, **patch}))
            self.assertEqual(doc.metadata, before)
            self.assertFalse(doc.dirty)

    def test_legacy_area_poly_uses_pixels_without_scaling(self):
        points = [[0, 0], [1920, 0], [1920, 1080], [0, 1080]]
        result = roi_fields({"area_poly": points}, 1920, 1080)
        self.assertEqual(result["area_roi"], points)
        self.assertNotIn("camera_id", result)
        self.assertNotIn("core_work_zone", result)

    def test_ass_regions_share_letterbox_transform_and_visibility(self):
        geometry = VideoGeometry.fit(1920, 1080, 960, 720)
        full = [[0, 0], [1920, 0], [1920, 1080], [0, 1080]]
        text = build_ass_overlay([], geometry, area_roi=full, core_work_zone=self.payload["core_work_zone"], area_id="station")
        self.assertIn("m 0.00 90.00 l 960.00 90.00 960.00 630.00 0.00 630.00", text)
        self.assertIn("ROI station", text)
        self.assertIn("核心作业区", text)
        self.assertEqual(build_ass_overlay([], geometry, area_roi=full, show_regions=False), "")


if __name__ == "__main__":
    unittest.main()
