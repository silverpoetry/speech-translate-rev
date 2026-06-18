from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.utils.audio.file_batch_domain import (
    FileBatchStatusContext,
    _build_export_plan,
    _resolve_alignment_language,
)


class FakeUiBridge:
    def __init__(self) -> None:
        self.calls = []

    def sync_file_status(self, index: int, status: str, is_completed: bool) -> None:
        self.calls.append((index, status, is_completed))


class AudioFileBatchDomainTests(unittest.TestCase):
    def test_status_context_syncs_combined_stage_state(self) -> None:
        bridge = FakeUiBridge()
        context = FileBatchStatusContext(is_tc=True, is_tl=True, ui_bridge=bridge)

        context.update_status("tc", 0, "Transcribed")
        context.update_status("tl", 0, "Translated")

        self.assertEqual(bridge.calls[-1], (0, "Transcribed, Translated", True))

    def test_status_context_surfaces_ui_sync_failures(self) -> None:
        class FailingUiBridge:
            def sync_file_status(self, index: int, status: str, is_completed: bool) -> None:
                raise RuntimeError(f"boom:{index}:{status}:{is_completed}")

        context = FileBatchStatusContext(is_tc=True, ui_bridge=FailingUiBridge())

        with self.assertRaisesRegex(RuntimeError, "boom:0:Transcribed:True"):
            context.update_status("tc", 0, "Transcribed")

    def test_build_export_plan_generates_metadata_path(self) -> None:
        export_plan = _build_export_plan(
            "D:\\exports",
            "{file}-{task}-{task-short}",
            {"{task}": "translated", "{task-short}": "tl"},
        )

        self.assertEqual(export_plan.save_name, "{file}-translated-tl")
        self.assertEqual(export_plan.metadata_path, "D:\\exports\\{file}-metadata-metadata.json")

    def test_resolve_alignment_language_ignores_short_or_missing_hint(self) -> None:
        self.assertIsNone(_resolve_alignment_language(["audio.wav", "result.json"]))
        self.assertIsNone(_resolve_alignment_language(["audio.wav", "result.json", "en"]))
        self.assertEqual(_resolve_alignment_language(["audio.wav", "result.json", "English"]), "english")


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
