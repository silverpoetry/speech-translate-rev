from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.utils.audio.record import (
    RealtimeSharedState,
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


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
