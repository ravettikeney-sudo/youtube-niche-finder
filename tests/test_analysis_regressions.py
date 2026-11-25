import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import asr
import export
import molecules
import thumbs
import video


def cues(text):
    return json.dumps({"events": [
        {"tStartMs": 4000 * n, "dDurationMs": 2000, "segs": [{"utf8": text}]}
        for n in range(12)
    ]}).encode()


class CaptionsTests(unittest.TestCase):
    def test_english_script_reads_only_english_track(self):
        info = {"language": "ar", "automatic_captions": {
            "ar-orig": [{"ext": "json3", "url": "https://example.com/ar"}],
            "en": [{"ext": "json3", "url": "https://example.com/ar?tlang=en"}],
            "en-US-orig": [{"ext": "json3", "url": "https://example.com/en"}],
        }}
        with patch.object(video, "_get", return_value=cues("Here is what happened in the city.")) as get:
            items = video.captions(info, "en", strict=True)
        get.assert_called_once_with("https://example.com/en")
        self.assertIn("the city", items[0][1])

    def test_foreign_and_mislabeled_tracks_do_not_enter_benchmark(self):
        info = {"language": "ar", "subtitles": {"ar": [{"ext": "json3", "url": "https://example.com/ar"}]}}
        with patch.object(video, "_get") as get:
            self.assertEqual(video.captions(info, "en", strict=True), [])
        get.assert_not_called()
        info["subtitles"]["en"] = info["subtitles"].pop("ar")
        with patch.object(video, "_get", return_value=cues("هذه مدينة حقيقية وليست لعبة")):
            self.assertEqual(video.captions(info, "en", strict=True), [])

    def test_rolling_vtt_counts_words_once(self):
        vtt = b"""WEBVTT

00:00.000 --> 00:02.000
[Music] You may think

00:01.900 --> 00:04.000
You may think it turns out

00:03.900 --> 00:05.000
it turns out you were right.
"""
        items = video._caption_items(vtt, "vtt")
        self.assertEqual(" ".join(t for _, t in items), "You may think it turns out you were right.")
        self.assertEqual(molecules.feats(items, 60)["words"], 9)

    def test_asr_does_not_force_foreign_speech_into_script_language(self):
        with patch.object(asr, "available", return_value=True), \
                patch.object(asr, "_download_audio", return_value=("audio", {})), \
                patch.object(asr, "_decode", return_value=np.zeros(16000 * 8)), patch.object(asr, "_get_model") as model:
            model.return_value.transcribe.return_value = (None, SimpleNamespace(language="ar", language_probability=0.95))
            self.assertIsNone(asr.transcribe("video", "en", strict=True))
            self.assertIsNone(model.return_value.transcribe.call_args.kwargs["language"])


class ThumbnailTests(unittest.TestCase):
    def test_numeric_text_is_recognized_as_text_with_or_without_face(self):
        img = np.zeros((180, 320, 3), dtype=np.uint8)
        data = {"text": ["0.5%", "FULL"], "conf": [71, 75], "width": [125, 145], "height": [49, 42]}
        with patch.object(thumbs, "_faces", return_value=[]), patch.object(thumbs, "pytesseract") as ocr:
            ocr.image_to_data.return_value = data
            f = thumbs.features(img)
        self.assertEqual(f["text"], "0.5% FULL")
        self.assertEqual(thumbs.style_of(f), "text_only")
        self.assertEqual(thumbs.style_of(dict(f, faces=1)), "face_text")

    def test_ocr_error_is_not_a_vote_for_no_text(self):
        img = np.zeros((180, 320, 3), dtype=np.uint8)
        with patch.object(thumbs, "_faces", return_value=[]), patch.object(thumbs, "pytesseract") as ocr:
            ocr.image_to_data.side_effect = RuntimeError("timeout")
            f = thumbs.features(img)
        sample = [dict(id=str(i), f=dict(f, faces=1), perf=2, title="Example", views=100) for i in range(3)]
        group = thumbs.analyze_group(sample)
        self.assertFalse(group["styles"])
        self.assertIsNone(group["recipe"]["text_share"])
        self.assertIn("текст не проверен", group["top"][0]["tags"])


class ScenarioTests(unittest.TestCase):
    def test_competitors_need_subject_and_channel_niche_even_from_report(self):
        channel = {"id": "own", "title": "Never Built", "tags": ["megaprojects urban planning"]}
        own = [{"id": str(i), "title": title, "duration": 600} for i, title in enumerate((
            "Abandoned desert city megaproject", "How a planned city failed", "Urban planning of a futuristic megaproject",
        ))]
        found = [dict(id=i, title=t, channel="Other", channel_id="other", duration=700, view_count=1000) for i, t in (
            ("number", "Survive 50 000 Days in a Forest"),
            ("game", "Building Masdar City in Cities Skylines"),
            ("other", "Masdar City: Apartment Tour"),
            ("documentary", "Abu Dhabi's Masdar City Megaproject"),
        )]
        info = {"game": {"categories": ["Gaming"]}, "other": {"description": "A tour of my apartment"},
                "documentary": {"categories": ["Education"], "description": "The urban planning behind the city"}}
        seed = {"competitor": {"title": "Other", "hit_videos": [{"id": "reported", "title": "50 000 in a Forest"}]}}
        with patch.object(molecules.A, "search", return_value=found), \
                patch.object(video, "full_info", side_effect=lambda vid, *_: info.get(vid, {"categories": ["Education"]})):
            matches, query, rejected = molecules.competitors("50 000: Masdar City", "", channel, own, "en@US", seed)
        self.assertEqual(query, "masdar city")
        self.assertEqual([v["id"] for v in matches], ["documentary"])
        self.assertEqual(rejected, 4)

    def test_rates_use_spoken_words_and_exact_duration(self):
        items = [(0, "You — may think differently."), (2, "It turns"), (4, "out your first assumption was wrong.")]
        f = molecules.feats(items, 15)
        self.assertEqual(f["words"], 12)
        self.assertEqual(f["rehook_pm"], 4)
        self.assertEqual(f["you_p100"], 16.67)
        self.assertIsNone(molecules.feats(items, 15, partial=True)["rehook_pm"])

    def test_no_same_language_examples_still_returns_script_and_duration(self):
        sentence = "This megaproject promised the city a new future, but its streets stayed remarkably quiet."
        script = " ".join([sentence] * 6)
        channel = {"id": "own", "title": "Never Built", "tags": ["urban planning"]}
        with patch.object(molecules.thumbs, "channel_videos", return_value=(channel, [])), \
                patch.object(molecules.A, "search", return_value=[]), patch.object(molecules.asr, "available", return_value=False):
            result = molecules.run(script, "https://www.youtube.com/@NeverBuilt")
        self.assertEqual(result["lang"], "en")
        self.assertIsNone(result["score"])
        self.assertEqual(result["hits_ex"], [])
        self.assertEqual(result["est_duration"], round(result["words"] * 60 / result["wpm"]))
        self.assertIn("Для оценки нужны хиты и провалы", export.molecules_md(result))

    def test_duration_at_148_words_per_minute_is_preserved(self):
        sents = [{"text": "word " * 148}, {"text": "word " * 148}]
        self.assertEqual(molecules.timed(sents, 148), 120)
        self.assertEqual([s["t"] for s in sents], [0, 60])

    def test_score_uses_own_comparable_videos_and_measured_pace(self):
        sentence = "This megaproject promised the city a new future, but its streets stayed remarkably quiet."
        script = " ".join([sentence] * 6)
        channel = {"id": "own", "title": "Never Built"}
        vids = [dict(id=str(i), title="Megaproject", views=v, duration=600, age=10) for i, v in enumerate(
            (32000, 28000, 10000, 10000, 10000, 10000, 3000, 2500)
        )]
        base = molecules.feats([(0, sentence)], 120)
        hits = [dict(base, id=str(i), title="Hit", wpm=148, greet=False, q20=False, num30=False) for i in range(2)]
        flops = [dict(base, id=str(i), title="Flop", wpm=148, greet=True, q20=True, num30=True) for i in range(6, 8)]
        with patch.object(molecules.thumbs, "channel_videos", return_value=(channel, vids)), \
                patch.object(molecules, "competitors", return_value=([], "", 0)), \
                patch.object(molecules, "_collect", side_effect=[(hits, []), (flops, []), ([], [])]), \
                patch.object(asr, "available", return_value=False):
            result = molecules.run(script, "https://www.youtube.com/@NeverBuilt")
        self.assertEqual(result["basis"]["score_metrics"], 3)
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["wpm"], 148)
        self.assertEqual(result["est_duration"], round(result["words"] * 60 / 148))
