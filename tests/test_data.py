"""Synthetic fixtures only: data safety, clock mapping, and sparse lookup."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from worker_annotator.data import AnnotationDocument, TimeMapping, TrackIndex, format_time, load_json


class DataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, payload):
        path = self.root / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def tracks(self, mapping=None):
        payload = {
            "schema_version": "stage2_pose_tracks_v1", "result_type": "pose_tracks",
            "identity_scope": "current_video_current_roi_run_only",
            "video_width": 1920, "video_height": 1080,
            "tracklets": [{"tracklet_id": "t1", "poses": [
                {"timestamp": t, "bbox": [10, 20, 50, 100],
                 "keypoints": [[11, 21], None], "keypoint_scores": [0.8, 0]}
                for t in [0, 0.5, 1, 10, 10.5]]}],
        }
        path = self.write("tracks.json", payload)
        return TrackIndex.from_file(path, mapping)

    def annotation(self, tracks=None):
        context = TimeMapping(40, 2, 8, 1).as_context("source.mp4")
        context["clip_to_source"]["future_flag"] = "keep"
        context["unknown_context"] = {"nested": [1, 2]}
        if tracks:
            context["pose_tracks_file"] = {"name": "tracks.json", "sha256": tracks.sha256, "vendor": "keep"}
        return {
            "schema_version": "stage2_worker_annotation_v1", "annotation_type": "worker_state_segments",
            "video_name": "synthetic.mp4", "video_width": 1920, "video_height": 1080,
            "camera_id": "cam", "area_roi": [[0, 0], [4, 0], [4, 4]],
            "unknown_root": {"nested": "保留"}, "annotation_context": context,
            "worker_segments": [
                {"tracklet_id": "t1", "start_time": 1, "end_time": 3,
                 "source_start_time": 42, "source_end_time": 46,
                 "worker_state": "working", "state_name": "人工原名", "confidence": 0.7,
                 "label_source": "human_legacy", "unknown_segment": {"x": 1}},
                {"tracklet_id": "t1", "start_time": 2.902, "end_time": 4,
                 "worker_state": "waiting_stagnant", "note": "conflict"},
            ],
        }

    def test_mapping_and_time_rounding(self):
        mapping = TimeMapping(20, 2, 5, 0.5)
        self.assertEqual(mapping.playback_to_source(3), 26)
        self.assertEqual(mapping.pose_to_playback(30), 0)
        self.assertEqual(TimeMapping.from_context(mapping.as_context()), mapping)
        self.assertEqual(format_time(3599.9996), "01:00:00.000")
        self.assertEqual(format_time(0.001), "00:00:00.001")
        self.assertEqual(format_time(360000), "100:00:00.000")
        for invalid in (float("nan"), float("inf"), -1):
            with self.assertRaises(ValueError):
                format_time(invalid)
        for values in ((0, 0, 0, 1), (0, 1, 0, -1), (float("nan"), 1, 0, 1)):
            with self.assertRaises(ValueError):
                TimeMapping(*values)
        with self.assertRaises(ValueError):
            TimeMapping.from_context({"version": 2})

    def test_sparse_lookup_keeps_pixels_and_missing_joints(self):
        tracks = self.tracks()
        self.assertEqual((tracks.width, tracks.height), (1920, 1080))
        self.assertEqual(tracks.sha256, hashlib.sha256(tracks.path.read_bytes()).hexdigest())
        self.assertEqual(tracks.ids, ["t1"])
        self.assertEqual(tracks.first_time("t1"), 0)
        self.assertEqual(tracks.last_time("t1"), 10.5)
        self.assertIsNone(tracks.first_time("missing"))
        pose = tracks.visible_at(0.6)[0]
        self.assertEqual(pose["bbox"], [10, 20, 50, 100])
        self.assertEqual(pose["keypoints"], [[11, 21], None])
        self.assertEqual(pose["keypoint_scores"], [0.8, 0])
        self.assertEqual(tracks.visible_at(5), [])
        self.assertEqual(tracks.visible_at(10.9), [])
        pose["keypoints"][0][0] = 999
        self.assertEqual(tracks.visible_at(0.6)[0]["keypoints"][0][0], 11)
        tracks.mapping = TimeMapping(2, 2, 0, 1)
        self.assertEqual(tracks.first_time("t1"), -1)
        self.assertEqual(tracks.visible_at(-0.7)[0]["timestamp"], 0.5)

    def test_gap_cap_even_for_very_sparse_tracks(self):
        tracks = self.tracks()
        payload = load_json(tracks.path)
        payload["tracklets"][0]["poses"] = payload["tracklets"][0]["poses"][::3]
        path = self.write("sparse.json", payload)
        index = TrackIndex.from_file(path)
        self.assertTrue(index.visible_at(0.5))
        self.assertEqual(index.visible_at(1), [])
        self.assertEqual(index.visible_at(5), [])

    def test_duplicate_ids_are_not_merged(self):
        tracks = self.tracks()
        payload = load_json(tracks.path)
        payload["tracklets"].append(payload["tracklets"][0])
        with self.assertRaisesRegex(ValueError, "重复"):
            TrackIndex.from_file(self.write("duplicate.json", payload))

    def test_existing_anonymous_scope_is_accepted_without_rewriting(self):
        tracks = self.tracks()
        payload = load_json(tracks.path)
        payload["identity_scope"] = "current_video_current_run_only_anonymous"
        path = self.write("anonymous.json", payload)
        original = path.read_bytes()
        index = TrackIndex.from_file(path)
        self.assertEqual(index.ids, ["t1"])
        self.assertEqual(index.metadata["identity_scope"], payload["identity_scope"])
        self.assertEqual(path.read_bytes(), original)
        payload["identity_scope"] = "cross_video_reidentification"
        with self.assertRaisesRegex(ValueError, "identity_scope"):
            TrackIndex.from_file(self.write("invalid-scope.json", payload))

    def test_exact_roundtrip_unknown_fields_and_conflict(self):
        tracks = self.tracks()
        payload = self.annotation(tracks)
        path = self.write("original.json", payload)
        original_bytes = path.read_bytes()
        document = AnnotationDocument()
        document.import_file(path, tracks)
        self.assertFalse(document.dirty)
        self.assertEqual(document.to_dict(tracks), payload)
        self.assertEqual(document.mapping, TimeMapping(40, 2, 8, 1))
        issues = document.validate(tracks, duration=3)
        self.assertTrue(any("重叠 0.098" in issue for issue in issues))
        self.assertTrue(any("超出视频时长" in issue for issue in issues))
        self.assertEqual(document.to_dict(tracks), payload)
        with self.assertRaisesRegex(ValueError, "原始"):
            document.save_as(path)
        output = self.root / "edited.json"
        document.save_as(output, tracks)
        self.assertEqual(load_json(output), payload)
        self.assertEqual(path.read_bytes(), original_bytes)

    def test_binding_mismatch_is_rejected_transactionally(self):
        tracks = self.tracks()
        payload = self.annotation(tracks)
        payload["annotation_context"]["pose_tracks_file"]["sha256"] = "f" * 64
        document = AnnotationDocument("existing.mp4")
        document.add_segment("existing", 1, 2, "unknown")
        before = document.to_dict()
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            document.import_file(self.write("wrong.json", payload), tracks)
        self.assertEqual(document.to_dict(), before)
        document.import_file(self.write("deferred.json", payload))
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            document.to_dict(tracks)
        self.assertTrue(any("SHA-256" in msg for msg in document.validate(tracks)))

    def test_add_edit_delete_preserves_segment_extensions(self):
        document = AnnotationDocument(mapping=TimeMapping(10, 2))
        added = document.add_segment("t1", 1.5, 2.75, "work_preparation", "manual")
        self.assertEqual(added["source_start_time"], 13)
        self.assertEqual(added["source_end_time"], 15.5)
        self.assertTrue(document.dirty)
        added["future"] = {"keep": True}
        document.update_segment(0, "t1", 2, 3, "working", "new")
        self.assertEqual(document.segments[0]["future"], {"keep": True})
        self.assertEqual(document.segments[0]["source_start_time"], 14)
        self.assertEqual(document.validate(), [])
        for a, b, state in ((2, 1, "working"), (-1, 1, "working"), (1, 2, "invalid")):
            with self.assertRaises(ValueError):
                document.add_segment("t1", a, b, state)
        document.delete_segment(0)
        self.assertEqual(document.segments, [])

    def test_imported_unknown_states_and_source_errors_are_retained(self):
        payload = self.annotation()
        payload["worker_segments"][0]["worker_state"] = "future_state"
        payload["worker_segments"][0]["source_start_time"] = 1234
        document = AnnotationDocument()
        document.import_file(self.write("unknown.json", payload))
        self.assertEqual(document.to_dict(), payload)
        issues = document.validate()
        self.assertTrue(any("状态代码未知" in issue for issue in issues))
        self.assertTrue(any("source_start_time" in issue for issue in issues))
        document.mapping = TimeMapping()
        with self.assertRaisesRegex(ValueError, "时间映射"):
            document.to_dict()

    def test_explicit_imported_edit_preserves_extensions_and_mapping(self):
        payload = self.annotation()
        document = AnnotationDocument()
        document.import_file(self.write("edit-original.json", payload))
        document.update_segment(0, "t1", 0, 1, "occluded", "explicit edit")
        edited = document.to_dict()
        self.assertEqual(edited["worker_segments"][0]["unknown_segment"], {"x": 1})
        self.assertEqual(edited["worker_segments"][0]["confidence"], 0.7)
        self.assertEqual(edited["worker_segments"][0]["source_start_time"], 40)
        self.assertEqual(edited["worker_segments"][0]["source_end_time"], 42)
        self.assertEqual(edited["worker_segments"][1], payload["worker_segments"][1])
        self.assertEqual(edited["annotation_context"], payload["annotation_context"])
        document.metadata["annotation_context"]["clip_to_source"]["scale"] = 3
        with self.assertRaisesRegex(ValueError, "映射"):
            document.to_dict()

    def test_atomic_save_failure_preserves_existing_destination(self):
        document = AnnotationDocument()
        document.add_segment("t1", 0, 1, "working")
        destination = self.root / "result.json"
        destination.write_text("original destination", encoding="utf-8")
        with patch("worker_annotator.data.os.replace", side_effect=OSError("synthetic failure")):
            with self.assertRaises(OSError):
                document.save_as(destination)
        self.assertEqual(destination.read_text(encoding="utf-8"), "original destination")
        self.assertTrue(document.dirty)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_utf8_bom_and_invalid_json(self):
        path = self.root / "bom.json"
        path.write_bytes(b"\xef\xbb\xbf" + b'{"synthetic": true}')
        self.assertEqual(load_json(path), {"synthetic": True})
        for raw in ('[1, 2]', '{"invalid": NaN}'):
            path.write_text(raw, encoding="utf-8")
            with self.assertRaises(ValueError):
                load_json(path)

    def test_malformed_state_is_reported_and_preserved(self):
        payload = self.annotation()
        payload["worker_segments"][0]["worker_state"] = ["malformed", "state"]
        document = AnnotationDocument()
        document.import_file(self.write("malformed-state.json", payload))
        self.assertTrue(any("状态代码未知" in issue for issue in document.validate()))
        self.assertEqual(document.to_dict(), payload)

    def test_empty_imported_document_can_explicitly_adopt_a_new_clock(self):
        payload = self.annotation()
        document = AnnotationDocument()
        document.import_file(self.write("clock-reset.json", payload))
        while document.segments:
            document.delete_segment(0)
        document.mapping = TimeMapping(100, 3)
        self.assertEqual(document.to_dict()["annotation_context"]["clip_to_source"]["offset_s"], 100)
        document.add_segment("t1", 0, 1, "working")
        exported = document.to_dict()
        self.assertEqual(exported["worker_segments"][0]["source_end_time"], 103)
        self.assertEqual(exported["annotation_context"]["clip_to_source"]["future_flag"], "keep")
        self.assertEqual(exported["unknown_root"], payload["unknown_root"])
        self.assertEqual(document.validate(), [])


if __name__ == "__main__":
    unittest.main()
