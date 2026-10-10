"""Offscreen UI workflow checks using synthetic JSON and a fake native player.

No video is decoded and no real labels, settings, or recovery folders are read.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QObject, QThread, QTimer, Signal, qInstallMessageHandler
from PySide6.QtWidgets import QApplication, QMessageBox

from worker_annotator import app as app_module
from worker_annotator.data import AnnotationDocument, TimeMapping, load_json


class FakeSettings:
    def __init__(self):
        self.values = {}

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


class FakeNativePlayer(QObject):
    position_changed = Signal(float)
    duration_changed = Signal(float)
    media_loaded = Signal(object)
    paused_changed = Signal(bool)
    error = Signal(str)

    def __init__(self, widget, parent=None):
        super().__init__(parent)
        self.position = 0.0
        self.duration = 15.0
        self.paused = True
        self.loaded_paths = []
        self.overlay = []
        self.closed = False

    def load(self, path):
        self.loaded_paths.append(path)
        self.media_loaded.emit({"width": 1920, "height": 1080, "duration": self.duration, "codec": "synthetic"})

    def seek(self, seconds):
        self.position = max(0, min(self.duration, float(seconds)))
        self.position_changed.emit(self.position)

    def seek_relative(self, seconds):
        self.seek(self.position + seconds)

    def set_pause(self, paused):
        self.paused = paused
        self.paused_changed.emit(paused)

    def toggle_pause(self):
        self.set_pause(not self.paused)

    def step_frame(self, backward=False):
        self.seek_relative(-0.04 if backward else 0.04)

    def set_speed(self, speed):
        self.speed = speed

    def set_overlay(self, poses, selected=None, **options):
        self.overlay = poses
        self.selected = selected
        self.options = options

    def shutdown(self):
        self.closed = True


def synchronous_job(window, message, fn, callback):
    """Replace background transport only; actual import and callbacks execute."""
    try:
        result = fn()
    except Exception as exc:
        window.show_error(str(exc))
        return
    callback(result)


class AppWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [
            patch.object(app_module, "NativePlayer", FakeNativePlayer),
            patch.object(app_module, "QSettings", FakeSettings),
            patch.object(app_module.AnnotationWindow, "_run_job", synchronous_job),
            patch.object(app_module.AnnotationWindow, "_set_recovery_path", lambda window: None),
            patch.object(app_module.AnnotationWindow, "_offer_recovery", lambda window: None),
            patch.object(app_module.QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok),
        ]
        for item in self.patches:
            item.start()
        self.window = app_module.AnnotationWindow()
        self.window.autosave.stop()
        self.video = self.root / "synthetic.mp4"
        # Merely an existence marker. FakeNativePlayer never opens these bytes.
        self.video.write_bytes(b"synthetic fixture; no valid media")
        self.tracks_path = self.write("tracks.json", {
            "schema_version": "stage2_pose_tracks_v1", "result_type": "pose_tracks",
            "identity_scope": "current_video_current_run_only_anonymous",
            "video_width": 1920, "video_height": 1080,
            "tracklets": [{"tracklet_id": "person-a", "poses": [
                {"timestamp": 0, "bbox": [10, 20, 100, 200]},
                {"timestamp": 0.5, "bbox": [10, 20, 100, 200]},
            ]}],
        })

    def tearDown(self):
        self.window.document.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.qt.processEvents()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def write(self, name, payload):
        path = self.root / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def open_inputs(self):
        self.assertTrue(self.window.open_video(self.video))
        self.window.import_tracks(self.tracks_path)
        self.assertIsNotNone(self.window.tracks)

    def labels(self, mapping=None):
        mapping = mapping or TimeMapping()
        context = mapping.as_context("synthetic.mp4")
        context["pose_tracks_file"] = {"name": "tracks.json", "sha256": self.window.tracks.sha256, "future_binding": 1}
        return {
            "schema_version": "stage2_worker_annotation_v1", "annotation_type": "worker_state_segments",
            "video_name": "synthetic.mp4", "video_width": 1920, "video_height": 1080,
            "annotation_context": context, "unknown_metadata": {"keep": True},
            "worker_segments": [{"tracklet_id": "person-a", "start_time": 1, "end_time": 3,
                                 "worker_state": "working", "state_name": "工作中",
                                 "source_start_time": mapping.playback_to_source(1),
                                 "source_end_time": mapping.playback_to_source(3),
                                 "unknown_label": [1, 2]}],
        }

    def test_initialization_and_no_implicit_labels(self):
        self.assertEqual(self.window.document.segments, [])
        self.assertIsNone(self.window.state_combo.currentData())
        self.open_inputs()
        self.assertEqual(self.window.document.segments, [])
        self.assertEqual(self.window.table.rowCount(), 0)
        self.assertEqual(self.window.target_list.count(), 1)
        self.assertEqual(self.window.player.loaded_paths, [str(self.video.resolve())])
        exported = self.window.document.to_dict(self.window.tracks)
        self.assertEqual((exported["video_width"], exported["video_height"]), (1920, 1080))
        self.assertNotIn("width", exported)
        self.assertNotIn("height", exported)

    def test_reuse_labels_preserves_unknown_fields_and_clock(self):
        self.open_inputs()
        payload = self.labels(TimeMapping(30, 2, 0, 1))
        path = self.write("labels.json", payload)
        before = path.read_bytes()
        self.window.import_labels(path)
        self.assertEqual(self.window.document.to_dict(self.window.tracks), payload)
        self.assertEqual(self.window.table.rowCount(), 1)
        self.assertEqual(self.window.tracks.mapping, TimeMapping(30, 2, 0, 1))
        self.assertFalse(self.window.document.dirty)
        self.assertEqual(path.read_bytes(), before)

    def test_mark_manual_interval_and_export_clock(self):
        self.open_inputs()
        meta = self.write("meta.json", {
            "source_video_name": "source.mp4", "pose_tracks_sha256": self.window.tracks.sha256,
            "annotation_tool_alignment": {"clip_offset_seconds": 10, "clip_scale": 2,
                                          "pose_offset_seconds": 0, "pose_scale": 1},
        })
        self.window.apply_meta(meta)
        self.window.select_target("person-a")
        self.window.player.seek(1)
        self.window.mark_start()
        self.window.player.seek(2.5)
        self.window.mark_end()
        self.window.state_combo.setCurrentIndex(self.window.state_combo.findData("working"))
        self.window.note.setText("人工标注")
        self.window.add_segment()
        self.assertEqual(len(self.window.document.segments), 1)
        destination = self.root / "new-labels.json"
        with patch.object(app_module.QFileDialog, "getSaveFileName", return_value=(str(destination), "JSON")):
            self.assertTrue(self.window.export_labels())
        segment = load_json(destination)["worker_segments"][0]
        self.assertEqual((segment["start_time"], segment["end_time"]), (1, 2.5))
        self.assertEqual((segment["source_start_time"], segment["source_end_time"]), (12, 15))
        self.assertEqual(segment["note"], "人工标注")
        self.assertFalse(self.window.document.dirty)

    def test_export_refuses_original_labels_and_cancel_is_nonmutating(self):
        self.open_inputs()
        original = self.write("labels.json", self.labels())
        self.window.import_labels(original)
        before = original.read_bytes()
        self.window.document.update_segment(0, "person-a", 1, 4, "occluded")
        with patch.object(app_module.QFileDialog, "getSaveFileName", return_value=(str(original), "JSON")):
            self.assertFalse(self.window.export_labels())
        self.assertEqual(original.read_bytes(), before)
        self.assertTrue(self.window.document.dirty)
        with patch.object(app_module.QFileDialog, "getSaveFileName", return_value=("", "")):
            self.assertFalse(self.window.export_labels())
        self.assertTrue(self.window.document.dirty)
        self.assertEqual(original.read_bytes(), before)

    def test_cancel_import_and_video_switch_preserves_current_document(self):
        self.open_inputs()
        self.window.document.add_segment("person-a", 1, 2, "working")
        before = self.window.document.to_dict()
        original_document = self.window.document
        labels = self.write("labels.json", self.labels())
        second_video = self.root / "second.mp4"
        second_video.write_bytes(b"unused")
        with patch.object(app_module.QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
            self.window.import_labels(labels)
            self.assertFalse(self.window.open_video(second_video))
        self.assertIs(self.window.document, original_document)
        self.assertEqual(self.window.document.to_dict(), before)
        self.assertEqual(self.window.video_path, self.video.resolve())
        self.assertEqual(len(self.window.player.loaded_paths), 1)

    def test_bad_label_binding_does_not_replace_document_or_tracks_mapping(self):
        self.open_inputs()
        before = self.window.document
        mapping = self.window.tracks.mapping
        payload = self.labels(TimeMapping(30, 2))
        payload["annotation_context"]["pose_tracks_file"]["sha256"] = "f" * 64
        with patch.object(self.window, "show_error") as error:
            self.window.import_labels(self.write("wrong-labels.json", payload))
        error.assert_called_once()
        self.assertIn("SHA-256", error.call_args.args[0])
        self.assertIs(self.window.document, before)
        self.assertEqual(self.window.tracks.mapping, mapping)

    def test_bad_meta_binding_is_transactional(self):
        self.open_inputs()
        mapping = self.window.document.mapping
        context = dict(self.window.document.metadata["annotation_context"])
        meta = self.write("wrong-meta.json", {
            "pose_tracks_sha256": "f" * 64,
            "annotation_tool_alignment": {"clip_offset_seconds": 80, "clip_scale": 2,
                                          "pose_offset_seconds": 0, "pose_scale": 1},
        })
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.window.apply_meta(meta)
        self.assertEqual(self.window.document.mapping, mapping)
        self.assertEqual(self.window.document.metadata["annotation_context"], context)
        self.assertEqual(self.window.tracks.mapping, mapping)
        self.assertIsNone(self.window._meta_expected_sha)

    def test_imported_invalid_interval_is_rendered_without_modifying_gt(self):
        self.open_inputs()
        payload = self.labels()
        payload["worker_segments"][0]["start_time"] = -1
        payload["worker_segments"][0].pop("end_time")
        original = self.write("invalid-times.json", payload)
        self.window.import_labels(original)
        self.assertEqual(self.window.document.to_dict(self.window.tracks), payload)
        self.assertEqual(self.window.table.rowCount(), 1)
        self.assertTrue(self.window.document.validate(self.window.tracks))

    def test_mismatched_label_dimensions_are_rejected_before_replacing(self):
        self.open_inputs()
        original_document = self.window.document
        original_mapping = self.window.tracks.mapping
        payload = self.labels(TimeMapping(30, 2))
        payload["video_width"] = 640
        path = self.write("wrong-dimensions.json", payload)
        before = path.read_bytes()
        with patch.object(self.window, "show_error") as error:
            self.window.import_labels(path)
        error.assert_called_once()
        self.assertIs(self.window.document, original_document)
        self.assertEqual(self.window.tracks.mapping, original_mapping)
        self.assertEqual(path.read_bytes(), before)

    def test_same_video_can_explicitly_reset_a_saved_document(self):
        self.open_inputs()
        self.window.document.add_segment("person-a", 1, 2, "working")
        self.window.document.dirty = False
        self.assertTrue(self.window.open_video(self.video))
        self.assertEqual(self.window.document.segments, [])
        self.assertIsNone(self.window.tracks)
        self.assertEqual(len(self.window.player.loaded_paths), 2)

    def test_malformed_state_is_displayed_without_altering_import(self):
        self.open_inputs()
        payload = self.labels()
        payload["worker_segments"][0]["worker_state"] = ["bad", "state"]
        path = self.write("malformed-state.json", payload)
        self.window.import_labels(path)
        self.assertEqual(self.window.document.to_dict(self.window.tracks), payload)
        self.assertEqual(self.window.table.rowCount(), 1)

    def wait_for_jobs(self, predicate, timeout_ms=5000):
        """Run real queued signals, bounded to five seconds per wait."""
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(10)
        deadline = QTimer()
        deadline.setSingleShot(True)
        completed = []

        def check():
            if predicate() and not self.window._jobs:
                completed.append(True)
                loop.quit()

        poll.timeout.connect(check)
        deadline.timeout.connect(loop.quit)
        poll.start()
        deadline.start(timeout_ms)
        loop.exec()
        poll.stop()
        deadline.stop()
        if not completed:
            # Workers in these tests only read tiny synthetic JSON; make a
            # failed assertion safe to tear down without destroying live Qt
            # threads or leaving a modal progress dialog behind.
            for thread, _job, progress in list(self.window._jobs):
                thread.quit()
                thread.wait(1000)
                progress.close()
            self.qt.processEvents()
        self.assertTrue(completed, "Queued imports did not finish within five seconds")
        self.assertEqual(self.window._jobs, [])

    def test_real_qthread_callbacks_and_chained_json_reads_run_on_ui_thread(self):
        self.patches[2].stop()  # Exercise the actual _run_job and _Job transport.
        self.assertTrue(self.window.open_video(self.video))
        source = AnnotationDocument("synthetic.mp4", 1920, 1080)
        source.add_segment("person-a", 1, 3, "working")
        payload = source.to_dict()
        payload["annotation_context"]["pose_tracks_file"] = {
            "name": "tracks.json", "sha256": hashlib.sha256(self.tracks_path.read_bytes()).hexdigest(),
        }
        labels = self.write("threaded-labels.json", payload)
        callbacks_on_main = []
        readers_on_main = []
        qt_messages = []
        original_reader = app_module.TrackIndex.from_file
        original_label_reader = app_module.AnnotationDocument.import_file
        original_refresh = self.window.refresh_segments

        def read_tracks(*args, **kwargs):
            readers_on_main.append(QThread.currentThread() == self.qt.thread())
            return original_reader(*args, **kwargs)

        def read_labels(document, *args, **kwargs):
            readers_on_main.append(QThread.currentThread() == self.qt.thread())
            return original_label_reader(document, *args, **kwargs)

        def refresh():
            callbacks_on_main.append(QThread.currentThread() == self.qt.thread())
            return original_refresh()

        def after_tracks():
            callbacks_on_main.append(QThread.currentThread() == self.qt.thread())
            self.window.import_labels(labels)

        previous_handler = qInstallMessageHandler(lambda kind, context, message: qt_messages.append(message))
        try:
            with patch.object(app_module.TrackIndex, "from_file", side_effect=read_tracks), \
                    patch.object(app_module.AnnotationDocument, "import_file", new=read_labels), \
                    patch.object(self.window, "refresh_segments", side_effect=refresh):
                self.window.import_tracks(self.tracks_path, after=after_tracks)
                self.wait_for_jobs(lambda: len(self.window.document.segments) == 1)
        finally:
            qInstallMessageHandler(previous_handler)
        self.assertEqual(readers_on_main, [False, False])
        self.assertGreaterEqual(len(callbacks_on_main), 2)
        self.assertTrue(all(callbacks_on_main))
        self.assertEqual(self.window.document.to_dict(self.window.tracks), payload)
        bad_messages = [message for message in qt_messages if any(text in message.lower() for text in
                        ("different thread", "cannot create children", "cannot set parent", "qthread: destroyed"))]
        self.assertEqual(bad_messages, [])
        self.assertTrue(self.window.close())
        self.assertTrue(self.window.player.closed)

    def test_real_qthread_import_failure_is_delivered_on_ui_thread_and_closes(self):
        self.patches[2].stop()
        self.assertTrue(self.window.open_video(self.video))
        received_on_main = []

        def record_warning(*args, **kwargs):
            received_on_main.append(QThread.currentThread() == self.qt.thread())
            return QMessageBox.StandardButton.Ok

        broken = self.root / "broken.json"
        broken.write_text("synthetic malformed JSON", encoding="utf-8")
        with patch.object(app_module.QMessageBox, "warning", side_effect=record_warning), \
                self.assertLogs(level="ERROR"):
            self.window.import_tracks(broken)
            self.wait_for_jobs(lambda: bool(received_on_main))
        self.assertEqual(received_on_main, [True])
        self.assertIsNone(self.window.tracks)
        self.assertEqual(self.window.document.segments, [])
        self.assertTrue(self.window.close())
        self.assertTrue(self.window.player.closed)


if __name__ == "__main__":
    unittest.main()
