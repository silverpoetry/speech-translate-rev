from __future__ import annotations

import os
import sys
import tempfile
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.utils.whisper import save as whisper_save
from speech_translate.utils.whisper.save_runtime_settings import build_whisper_save_runtime_settings


class FakeSegment:
    def __init__(self, text: str, start: float, end: float) -> None:
        self.text = text
        self.start = start
        self.end = end


class FakeResult:
    def __init__(self) -> None:
        self.segments = [FakeSegment("hello", 0.0, 1.0)]
        self.calls: list[tuple[str, dict[str, object]]] = []

    def to_dict(self):
        return {"segments": [{"text": "hello", "start": 0.0, "end": 1.0}]}

    def to_tsv(self, **kwargs):
        self.calls.append(("to_tsv", dict(kwargs)))

    def to_srt_vtt(self, **kwargs):
        self.calls.append(("to_srt_vtt", dict(kwargs)))


class WhisperSaveTests(unittest.TestCase):
    def test_build_whisper_save_runtime_settings_extracts_export_levels(self) -> None:
        settings = build_whisper_save_runtime_settings(
            {
                "whisper_args": "--save_option highlight_color=ffffff",
                "segment_level": False,
                "word_level": True,
            }
        )

        self.assertEqual(settings.whisper_args, "--save_option highlight_color=ffffff")
        self.assertFalse(settings.segment_level)
        self.assertTrue(settings.word_level)

    def test_fname_dupe_check_uses_extension_when_detecting_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = os.path.join(temp_dir, "clip")
            with open(base + ".json", "w", encoding="utf-8") as handle:
                handle.write("{}")

            self.assertEqual(whisper_save.fname_dupe_check(base, "json"), base + " (2)")

    def test_save_output_stable_ts_uses_typed_runtime_settings_for_tsv(self) -> None:
        previous_parser = whisper_save.parse_args_stable_ts
        result = FakeResult()
        settings = build_whisper_save_runtime_settings(
            {
                "whisper_args": "--save_option tag=test",
                "segment_level": True,
                "word_level": True,
            }
        )
        try:
            whisper_save.parse_args_stable_ts = lambda *_args, **kwargs: {"success": True, **kwargs}
            with tempfile.TemporaryDirectory() as temp_dir:
                whisper_save.save_output_stable_ts(result, os.path.join(temp_dir, "clip"), ["tsv"], settings)
        finally:
            whisper_save.parse_args_stable_ts = previous_parser

        self.assertEqual(len(result.calls), 1)
        method_name, kwargs = result.calls[0]
        self.assertEqual(method_name, "to_tsv")
        self.assertEqual(kwargs["save_path"], os.path.join(temp_dir, "clip"))
        self.assertTrue(kwargs["word_level"])
        self.assertFalse(kwargs["segment_level"])

    def test_save_output_stable_ts_json_uses_single_collision_adjustment(self) -> None:
        result = FakeResult()
        settings = build_whisper_save_runtime_settings({"whisper_args": "", "segment_level": True, "word_level": False})
        with tempfile.TemporaryDirectory() as temp_dir:
            base = os.path.join(temp_dir, "clip")
            with open(base + ".json", "w", encoding="utf-8") as handle:
                handle.write("{}")

            whisper_save.save_output_stable_ts(result, base, ["json"], settings)

            self.assertTrue(os.path.exists(base + ".json"))
            self.assertTrue(os.path.exists(base + " (2).json"))
            self.assertFalse(os.path.exists(base + " (3).json"))


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
