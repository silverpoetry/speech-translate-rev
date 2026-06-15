from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.utils.audio.file import (
    FileBatchStatusContext,
    WorkerFailure,
    _apply_task_format,
    _build_base_export_name,
    _build_combined_status,
    _build_metadata_name,
    _is_file_status_completed,
)
from speech_translate.linker import bc


class FakeFileStatusBridge:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.calls = []

    def sync_file_status(self, index: int, status: str, is_completed: bool) -> None:
        self.calls.append((index, status, is_completed))
        if self.should_fail:
            raise RuntimeError("bridge failed")


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

    def test_build_base_export_name_replaces_standard_tokens(self) -> None:
        result = _build_base_export_name(
            "{file}-{lang-source}-{lang-target}-{transcribe-with}-{translate-with}",
            "clip",
            "English",
            "Chinese",
            "small",
            "Google Translate",
        )
        self.assertEqual(result, "clip-English-Chinese-small-Google Translate")

    def test_build_metadata_name_rewrites_task_tokens(self) -> None:
        result = _build_metadata_name("{file}-{task}-{task-short}")
        self.assertEqual(result, "{file}-metadata-metadata")

    def test_apply_task_format_rewrites_save_name_tokens(self) -> None:
        result = _apply_task_format("{file}-{task}-{task-short}", {"{task}": "translated", "{task-short}": "tl"})
        self.assertEqual(result, "{file}-translated-tl")

    def test_file_batch_status_context_updates_stage_status_and_syncs_bridge(self) -> None:
        previous_bridge = bc.web_bridge
        bridge = FakeFileStatusBridge()
        try:
            bc.web_bridge = bridge
            context = FileBatchStatusContext(is_tc=True, is_tl=True)
            context.update_status("tc", 0, "Transcribed")
            context.update_status("tl", 0, "Translated")
        finally:
            bc.web_bridge = previous_bridge

        self.assertEqual(context.tc_status[0], "Transcribed")
        self.assertEqual(context.tl_status[0], "Translated")
        self.assertEqual(bridge.calls[-1], (0, "Transcribed, Translated", True))

    def test_file_batch_status_context_suppresses_bridge_sync_errors(self) -> None:
        previous_bridge = bc.web_bridge
        bridge = FakeFileStatusBridge(should_fail=True)
        try:
            bc.web_bridge = bridge
            context = FileBatchStatusContext(is_mod=True)
            context.update_status("mod", 3, "Processing")
        finally:
            bc.web_bridge = previous_bridge

        self.assertEqual(context.mod_status[3], "Processing")
        self.assertEqual(bridge.calls[-1], (3, "Processing", False))


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
