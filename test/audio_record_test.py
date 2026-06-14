from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.utils.audio.record import (
    RealtimeSharedState,
    TranslationTask,
    _build_recording_state_payload,
    _build_full_transcribed_text,
    _result_text,
)


class FakeResult:
    def __init__(self, text: str) -> None:
        self.text = text


class AudioRecordHelpersTests(unittest.TestCase):
    def test_result_text_supports_result_object_and_string(self) -> None:
        self.assertEqual(_result_text(FakeResult(" hello ")), "hello")
        self.assertEqual(_result_text(" world "), "world")
        self.assertEqual(_result_text(None), "")

    def test_build_full_transcribed_text_joins_sentence_history(self) -> None:
        sentences = [FakeResult("alpha"), " beta ", FakeResult("   ")]
        combined = _build_full_transcribed_text(sentences, FakeResult("gamma"))
        self.assertEqual(combined, "alpha\nbeta\ngamma")

    def test_shared_state_defaults_are_empty(self) -> None:
        state = RealtimeSharedState()
        self.assertEqual(state.prev_tc_res, "")
        self.assertEqual(state.prev_tl_res, "")
        self.assertIsNone(state.last_db)

    def test_translation_task_defaults_are_explicit(self) -> None:
        task = TranslationTask(kind="whisper", separator="<br />")
        self.assertEqual(task.kind, "whisper")
        self.assertEqual(task.separator, "<br />")
        self.assertIsNone(task.audio)
        self.assertFalse(task.cleanup_audio)
        self.assertEqual(task.text, "")

    def test_build_recording_state_payload_omits_optional_fields_when_missing(self) -> None:
        payload = _build_recording_state_payload(
            status="Recording",
            device="mic",
            lang_source="English",
            lang_target="Chinese",
            engine="Google Translate",
            mode="Transcribe & Translate",
        )
        self.assertEqual(
            payload,
            {
                "status": "Recording",
                "device": "mic",
                "lang_source": "English",
                "lang_target": "Chinese",
                "engine": "Google Translate",
                "mode": "Transcribe & Translate",
            },
        )

    def test_build_recording_state_payload_includes_optional_fields(self) -> None:
        payload = _build_recording_state_payload(
            status="Recording",
            device="speaker",
            lang_source="English",
            lang_target="-",
            engine="Whisper",
            mode="Translate",
            timer="00:00:10",
            buffer_text="1.2/10.0 sec",
            sentences="3/5",
        )
        self.assertEqual(payload["timer"], "00:00:10")
        self.assertEqual(payload["buffer"], "1.2/10.0 sec")
        self.assertEqual(payload["sentences"], "3/5")


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
