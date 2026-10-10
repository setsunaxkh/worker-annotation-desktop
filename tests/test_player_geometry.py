import os
import unittest
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from worker_annotator.player import NativePlayer, VideoGeometry, VideoWidget, build_ass_overlay


class PlayerGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def assertPointAlmostEqual(self, actual, expected):
        self.assertIsNotNone(actual)
        for a, b in zip(actual, expected):
            self.assertAlmostEqual(a, b)

    def test_landscape_letterbox_and_black_bar_rejection(self):
        geometry = VideoGeometry.fit(1920, 1080, 1000, 1000)
        self.assertAlmostEqual(geometry.y, 218.75)
        self.assertAlmostEqual(geometry.height, 562.5)
        self.assertIsNone(geometry.widget_to_source(500, 200))
        self.assertIsNone(geometry.widget_to_source(1000, 500))
        self.assertPointAlmostEqual(geometry.widget_to_source(500, 500), (960, 540))
        self.assertPointAlmostEqual(geometry.source_to_widget(1920, 1080), (1000, 781.25))

    def test_portrait_and_anamorphic_coordinates_round_trip(self):
        geometry = VideoGeometry.fit(1080, 1920, 1280, 720)
        self.assertAlmostEqual(geometry.width, 405)
        self.assertIsNone(geometry.widget_to_source(0, 360))
        for point in [(0, 0), (180, 320), (1079, 1919)]:
            self.assertPointAlmostEqual(geometry.widget_to_source(*geometry.source_to_widget(*point)), point)
        anamorphic = VideoGeometry.fit(720, 576, 1280, 720, 16 / 9)
        self.assertEqual(anamorphic.source_to_widget(360, 288), (640, 360))

    def test_unknown_dimensions_never_allow_selection(self):
        self.assertIsNone(VideoGeometry.fit(0, 0, 1280, 720).widget_to_source(640, 360))

    def test_seek_publishes_actual_presentation_timestamp_not_requested_target(self):
        widget = VideoWidget()
        player = NativePlayer(widget)
        player._mpv = SimpleNamespace(time_pos=12.357)
        player._seeking = True
        positions = []
        player.position_changed.connect(positions.append)
        player._receive('time-pos', 12.345)
        self.assertEqual(positions, [])
        player._receive('seeking', False)
        self.assertEqual(positions, [12.357])
        self.assertEqual(player.position, 12.357)
        player._mpv = None
        player.shutdown()
        widget.close()

    def test_widget_native_margins_and_high_dpi_match_overlay(self):
        widget = VideoWidget()
        widget.resize(1000, 1000)
        widget.source_dimensions(1920, 1080)
        widget._osd_dimensions = {'w': 2000, 'h': 2000, 'ml': 0, 'mr': 0, 'mt': 438, 'mb': 438}
        self.assertPointAlmostEqual(widget.source_to_widget(960, 540), (500, 500))
        self.assertIsNone(widget.widget_to_source(500, 200))
        clicks = []
        widget.clicked.connect(lambda x, y: clicks.append((x, y)))
        widget._click_at(500, 500)
        widget._click_at(500, 100)
        self.assertEqual(clicks, [(960, 540)])
        overlay = build_ass_overlay([{'tracklet_id': 'a', 'bbox': [0, 0, 1920, 1080]}], widget.geometry_mapping)
        self.assertIn('0.00 219.00', overlay)
        self.assertIn('1000.00 781.00', overlay)
        widget.close()

    def test_skeleton_requires_present_confidence_and_never_fills_missing_joints(self):
        geometry = VideoGeometry.fit(100, 100, 100, 100)
        points = [[10 + i, 20 + i] for i in range(17)]
        self.assertEqual(build_ass_overlay([{'keypoints': points}], geometry, show_boxes=False, show_skeleton=True), '')
        scores = [0.0] * 17
        scores[5] = .9
        scores[7] = .29
        pose = {'keypoints': points, 'keypoint_scores': scores}
        self.assertEqual(len(build_ass_overlay([pose], geometry, show_boxes=False, show_skeleton=True).splitlines()), 1)
        scores[7] = .3
        self.assertEqual(len(build_ass_overlay([pose], geometry, show_boxes=False, show_skeleton=True).splitlines()), 3)
        pose['keypoints'][5] = [0, 0, .99]
        self.assertEqual(len(build_ass_overlay([pose], geometry, show_boxes=False, show_skeleton=True).splitlines()), 1)

    def test_nonfinite_bbox_is_skipped_and_ids_cannot_inject_ass_tags(self):
        geometry = VideoGeometry.fit(100, 100, 100, 100)
        self.assertEqual(build_ass_overlay([{'bbox': [0, 0, float('nan'), 50]}], geometry), '')
        pose = {'tracklet_id': '{\\p1}\nother', 'bbox': [10, 10, 50, 50]}
        overlay = build_ass_overlay([pose], geometry, selected_id=pose['tracklet_id'])
        self.assertEqual(len(overlay.splitlines()), 5)
        self.assertIn('00D7FF', overlay)
        self.assertIn('\\{', overlay)


if __name__ == '__main__':
    unittest.main()
