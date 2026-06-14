from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.utils.audio.record import (
    RealtimeSharedState,
    TranslationDispatcher,
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

    def test_translation_dispatcher_queues_whisper_task(self) -> None:
        updates = []
        dispatcher = TranslationDispatcher(
            is_tl=True,
            tl_engine_whisper=True,
            use_temp=True,
            keep_temp=False,
            separator="<br />",
            lang_source="English",
            lang_target="Chinese",
            engine="Whisper",
            hallucination_filters={},
            stable_tl=object(),
            whisper_args={},
            record_status_updater=lambda: updates.append("u"),
        )
        dispatcher.dispatch("temp.wav", "")
        task = dispatcher._queue.get_nowait()
        self.assertEqual(task.kind, "whisper")
        self.assertEqual(task.audio, "temp.wav")
        self.assertTrue(task.cleanup_audio)

    def test_translation_dispatcher_replaces_duplicate_api_task_by_text(self) -> None:
        dispatcher = TranslationDispatcher(
            is_tl=True,
            tl_engine_whisper=False,
            use_temp=False,
            keep_temp=False,
            separator="<br />",
            lang_source="English",
            lang_target="Chinese",
            engine="Google Translate",
            hallucination_filters={},
            stable_tl=object(),
            whisper_args={},
            record_status_updater=lambda: None,
        )
        dispatcher.dispatch(None, "hello")
        first_task = dispatcher._latest_api_task
        dispatcher.dispatch(None, "hello")
        self.assertIsNotNone(first_task)
        self.assertEqual(dispatcher._latest_api_task.text, first_task.text)
        self.assertEqual(dispatcher._latest_api_task.lang_target, first_task.lang_target)
        dispatcher.dispatch(None, "world")
        self.assertEqual(dispatcher._latest_api_task.text, "world")


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
