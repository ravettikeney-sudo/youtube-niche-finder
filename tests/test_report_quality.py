import datetime as dt
import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

import analyzer
import brief
import export
import ideas
import molecules
import radar
import reportview
import saturation
import verify


class EvidenceTests(unittest.TestCase):
    def test_search_deduplicates_and_keeps_measured_zero(self):
        seen = verify.unique_videos([{"id": "a", "view_count": None}, {"id": "b", "view_count": 0},
                                    {"id": "a", "view_count": 10000}, {"id": "a", "view_count": 10000}])
        self.assertEqual({row["id"]: row["view_count"] for row in seen}, {"a": 10000, "b": 0})
        self.assertFalse(verify.data_sufficient([], [100] * 12))
        self.assertFalse(verify.data_sufficient([{}, {}, {}], []))

    def test_mismatched_channel_is_detected_on_video_page(self):
        listed = {"id": "expected", "title": "Channel", "_ents": [{"id": "a", "view_count": 700}]}
        full = {"id": "a", "channel_id": "another", "upload_date": "20261124", "view_count": 700}
        with patch.object(analyzer, "_extract", return_value=full):
            outcome = verify.crosscheck([listed], analyzer.video_info)
        self.assertTrue(outcome[0]["wrong_channel"])

    def test_format_comes_from_youtube_not_duration(self):
        self.assertEqual(analyzer.video_format({"url": "https://www.youtube.com/shorts/video", "duration": 150}), "shorts")
        self.assertEqual(analyzer.video_format({"duration": 30}, "videos"), "long")
        self.assertEqual(analyzer.video_format({"duration": 150}), "unknown")
        self.assertEqual(analyzer.video_format({"live_status": "was_live"}, "videos"), "live")

    def test_excluded_channel_withdraws_score_without_enough_data(self):
        report = {"niche": {"overall": 75, "rpm_range": [2, 4]}, "competitor": {"title": "Example", "url": "https://example.com/seed"},
                  "channels": [{"id": k, "relevant": True, "url": "https://example.com/" + k}
                               for k in ("a", "b", "c")], "excluded": ["b"],
                  "_score_basis": {"search": [["a", 10], ["a", 20], ["b", 10000], ["b", 10000], ["c", 30]]}}
        edited = reportview.build(report)
        self.assertIsNone(edited["niche"]["overall"])
        self.assertEqual(edited["niche"]["median_search_views"], 20)
        self.assertEqual(edited["niche"]["relevant"], 2)
        self.assertEqual(report["niche"]["overall"], 75)
        self.assertNotIn("_score_basis", edited)
        self.assertIn("Мало проверенных данных", export.niche_md(dict(edited, channels=[])))

    def test_legacy_exclusion_preserves_report_without_fake_recalculation(self):
        report = {"niche": {"overall": 65, "rpm_range": [2, 4]}, "excluded": ["a"],
                  "channels": [{"id": "a", "relevant": True}]}
        edited = reportview.build(report)
        self.assertIsNone(edited["niche"]["overall"])
        self.assertIn("старого отчёта", edited["niche"]["verdict"])

    def test_unavailable_ocr_preserves_preview_summary(self):
        report = {"type": "thumbs", "channel": {"title": "Channel"},
                  "comp": {"recipe": {"n": 5, "face_share": 40, "text_share": None}}}
        self.assertTrue(brief.build(report)["items"])

    def test_intro_stops_at_requested_time(self):
        sents = [{"text": "ordinary " * 80 + "Masdar City?"}]
        dur = molecules.timed(sents, 148)
        result = molecules.feats([(0, sents[0]["text"])], dur, "Masdar City", wpm=148)
        self.assertFalse(result["topic15"])
        self.assertFalse(result["q20"])
        self.assertNotIn("Masdar", result["first30"])


class FreshnessTests(unittest.TestCase):
    def test_todays_upload_is_fresh_and_empty_search_is_unknown(self):
        today = dt.date(2026, 11, 25)
        upload = {"id": "x", "title": "Masdar City", "upload_date": "20261125", "view_count": 1000}
        with patch.object(analyzer, "search", return_value=[upload, upload]):
            result = ideas.demand("Masdar City", "en@US", "lat", 100, today)
        self.assertEqual(result["fresh"], 1)
        with patch.object(saturation, "_search_dated", return_value=[]):
            self.assertIsNone(saturation.copies("Masdar City Desert Megaproject"))

    def test_radar_requires_distinct_comparable_videos(self):
        points = [{"vid": str(i), "age_h": 25, "precise": 1, "views": 1000} for i in range(4)]
        self.assertEqual(radar.measured_ratio("new", 24, 1, 3500, points), 3.5)
        self.assertIsNone(radar.measured_ratio("new", 24, 1, 3500, [points[0]] * 4))
        self.assertIsNone(radar.measured_ratio("new", 24, 0, 3500, points))
        self.assertIsNone(radar.measured_ratio("new", 480, 1, 3500, points))

    def test_radar_does_not_convert_old_publication_into_new_discovery(self):
        now = 1_800_000_000
        saved = sqlite3.connect(":memory:")
        self.addCleanup(saved.close)
        saved.row_factory = sqlite3.Row

        @contextmanager
        def db():
            with saved:
                yield saved

        channel = {"id": "c", "title": "Channel"}
        watched = {"id": "r", "active": True, "last_run": now - 14400, "channels": [channel],
                   "code_hash": "test", "title": "Test"}
        old = {"id": "old", "title": "Old", "views": 35000, "ts": now - 20 * 86400, "precise": 1}
        snapshot = {"total": None, "subs": None, "vids": [old]}
        notify = Mock()
        with patch.object(radar.access, "_db", db), patch.object(radar, "get", return_value=watched), \
                patch.object(radar, "_check_channel", return_value=snapshot), patch.object(radar.time, "time", return_value=now):
            radar.init()
            saved.execute("INSERT INTO radars(id, running) VALUES ('r', 0)")
            radar.run("r", notify)
            row = saved.execute("SELECT published, rocket_at FROM radar_vids").fetchone()
        self.assertEqual(row["published"], old["ts"])
        self.assertIsNone(row["rocket_at"])
        notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
