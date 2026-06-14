from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.utils.audio.file import (
    WorkerFailure,
    _build_combined_status,
    _is_file_status_completed,
)


class AudioFileHelpersTests(unittest.TestCase):
    def test_build_combined_status_merges_active_statuses(self) -> None:
        status = _build_combined_status(
            0,
            is_tc=True,
            is_tl=True,
            is_mod=False,
            tc_status={0: "Transcribed"},
            tl_status={0: "Translated"},
            mod_status={},
        )
        self.assertEqual(status, "Transcribed, Translated")

    def test_build_combined_status_omits_waiting_entries(self) -> None:
        status = _build_combined_status(
            1,
            is_tc=True,
            is_tl=False,
            is_mod=False,
            tc_status={1: "Waiting"},
            tl_status={},
            mod_status={},
        )
        self.assertEqual(status, "Waiting")

    def test_is_file_status_completed_for_dual_stage_work(self) -> None:
        combined = "Transcribed, Translated"
        self.assertTrue(
            _is_file_status_completed(
                0,
                combined,
                is_tc=True,
                is_tl=True,
                is_mod=False,
                tc_status={0: "Transcribed"},
                tl_status={0: "Translated"},
                mod_status={},
            )
        )

    def test_is_file_status_completed_for_error_status(self) -> None:
        self.assertTrue(
            _is_file_status_completed(
                0,
                "Parse Error",
                is_tc=False,
                is_tl=False,
                is_mod=True,
                tc_status={},
                tl_status={},
                mod_status={0: "Parse Error"},
            )
        )

    def test_worker_failure_raises_captured_error(self) -> None:
        failure = WorkerFailure()
        captured = RuntimeError("boom")
        failure.capture(captured)
        with self.assertRaises(RuntimeError) as ctx:
            failure.raise_if_failed()
        self.assertIs(ctx.exception, captured)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
