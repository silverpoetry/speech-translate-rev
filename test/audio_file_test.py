from __future__ import annotations

import os
from queue import Queue
import tempfile
import sys
import unittest
from unittest.mock import Mock, patch

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.utils.audio.file import (
    FileBatchStatusContext,
    FileExportPlan,
    WorkerFailure,
    _execute_monitored_queue_task,
    _apply_task_format,
    _build_base_export_name,
    _build_combined_status,
    _build_export_plan,
    _build_metadata_name,
    _build_mod_result_runtime,
    _build_process_file_runtime,
    _build_translate_result_runtime,
    _is_file_status_completed,
    _save_export_plan_metadata,
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
    def test_build_process_file_runtime_collects_shared_runtime_state(self) -> None:
        fake_stable_tc = object()
        fake_stable_tl = object()
        setting_cache = {
            "dir_export": "D:\\exports",
            "file_slice_start": "1",
            "file_slice_end": "4",
            "path_filter_file_import": "D:\\filters\\input.json",
            "filter_file_import": True,
        }

        with (
            patch("speech_translate.utils.audio.file.get_model_args", return_value={"device": "cpu"}),
            patch(
                "speech_translate.utils.audio.file.get_model",
                return_value=(None, None, fake_stable_tc, fake_stable_tl, "transcribe-api"),
            ),
            patch("speech_translate.utils.audio.file.get_tc_args", return_value={"temperature": 0.2}),
            patch("speech_translate.utils.audio.file.get_whisper_lang_similar", return_value="english"),
            patch("speech_translate.utils.audio.file.get_whisper_to_language_code", return_value={"english": "en"}),
            patch("speech_translate.utils.audio.file.get_hallucination_filter", return_value={"ban": ["uh"]}),
            patch("speech_translate.utils.audio.file.time", return_value=42.0),
        ):
            runtime = _build_process_file_runtime(
                model_name_tc="small",
                lang_source="English",
                engine="Google Translate",
                is_tc=True,
                is_tl=False,
                setting_cache=setting_cache,
            )

        self.assertEqual(runtime.export_dir, "D:\\exports")
        self.assertEqual((runtime.slice_start, runtime.slice_end), (1, 4))
        self.assertEqual(runtime.taskname, "Transcribe")
        self.assertFalse(runtime.tl_engine_whisper)
        self.assertIs(runtime.stable_tc, fake_stable_tc)
        self.assertIs(runtime.stable_tl, fake_stable_tl)
        self.assertEqual(runtime.whisper_args["temperature"], 0.2)
        self.assertEqual(runtime.whisper_args["language"], "en")
        self.assertIsNone(runtime.whisper_args["verbose"])
        self.assertEqual(runtime.filters, {"ban": ["uh"]})
        self.assertEqual(runtime.started_at, 42.0)

    def test_build_mod_result_runtime_selects_mode_specific_dependencies(self) -> None:
        fake_model = Mock()
        fake_model.refine = Mock(name="refine")
        fake_model.align = Mock(name="align")
        fake_stable_whisper = Mock()
        fake_stable_whisper.load_model.return_value = fake_model
        setting_cache = {
            "dir_export": "auto",
            "file_slice_start": "",
            "file_slice_end": "6",
        }

        with (
            patch("speech_translate.utils.audio.file.get_stable_whisper", return_value=fake_stable_whisper),
            patch("speech_translate.utils.audio.file.get_model_args", return_value={"device": "cpu"}),
            patch("speech_translate.utils.audio.file.get_tc_args", return_value={"steps": 2}),
            patch("speech_translate.utils.audio.file.time", return_value=84.0),
        ):
            runtime = _build_mod_result_runtime(
                model_name_tc="medium",
                mode="alignment",
                setting_cache=setting_cache,
            )

        self.assertEqual(runtime.action, "Alignment")
        self.assertTrue(os.path.normpath(runtime.export_dir).endswith("@aligned"))
        self.assertEqual((runtime.slice_start, runtime.slice_end), (None, 6))
        self.assertIs(runtime.stable_whisper_api, fake_stable_whisper)
        self.assertIs(runtime.model, fake_model)
        self.assertIs(runtime.mod_func, fake_model.align)
        self.assertEqual(runtime.mod_args, {"steps": 2})
        self.assertEqual(runtime.started_at, 84.0)

    def test_build_translate_result_runtime_scopes_engine_specific_api_kwargs(self) -> None:
        fake_stable_whisper = object()
        setting_cache = {
            "dir_export": "D:\\exports",
            "file_slice_start": "2",
            "file_slice_end": "",
            "libre_link": "http://127.0.0.1:5000",
            "libre_api_key": "secret",
        }

        with (
            patch("speech_translate.utils.audio.file.get_stable_whisper", return_value=fake_stable_whisper),
            patch("speech_translate.utils.audio.file.time", return_value=126.0),
        ):
            runtime = _build_translate_result_runtime(
                engine="LibreTranslate",
                setting_cache=setting_cache,
            )

        self.assertEqual(os.path.normpath(runtime.export_dir), os.path.normpath("D:\\exports\\@translated"))
        self.assertEqual((runtime.slice_start, runtime.slice_end), (2, None))
        self.assertIs(runtime.stable_whisper_api, fake_stable_whisper)
        self.assertEqual(
            runtime.api_kwargs,
            {"libre_link": "http://127.0.0.1:5000", "libre_api_key": "secret"},
        )
        self.assertEqual(runtime.started_at, 126.0)

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

    def test_build_export_plan_uses_base_name_for_metadata_and_formatted_save_name(self) -> None:
        export_plan = _build_export_plan(
            "D:\\exports",
            "{file}-{task}-{task-short}",
            {"{task}": "translated", "{task-short}": "tl"},
        )

        self.assertIsInstance(export_plan, FileExportPlan)
        self.assertEqual(export_plan.save_name, "{file}-translated-tl")
        self.assertEqual(export_plan.save_base_path, "D:\\exports\\{file}-translated-tl")
        self.assertEqual(export_plan.metadata_path, "D:\\exports\\{file}-metadata-metadata.json")

    def test_save_export_plan_metadata_merges_existing_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            export_plan = FileExportPlan(
                export_dir=temp_dir,
                base_name="clip",
                save_name="clip-translated",
                metadata_path=os.path.join(temp_dir, "clip.json"),
            )
            _save_export_plan_metadata(export_plan, {"task": "initial", "ok": True})
            _save_export_plan_metadata(export_plan, {"time": 1.5})

            with open(export_plan.metadata_path, "r", encoding="utf-8") as file:
                saved = file.read()

        self.assertIn('"task": "initial"', saved)
        self.assertIn('"ok": true', saved)
        self.assertIn('"time": 1.5', saved)

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

    def test_file_batch_status_context_has_active_work_aggregates_enabled_stages(self) -> None:
        context = FileBatchStatusContext(
            is_tc=True,
            is_tl=True,
            tc_status={0: "Transcribed"},
            tl_status={0: "Translated", 1: "Translating please wait..."},
        )

        self.assertTrue(context.has_active_work(2))
        self.assertFalse(context.has_active_work(1))

    def test_execute_monitored_queue_task_returns_background_result(self) -> None:
        previous_queue = bc.data_queue
        try:
            bc.data_queue = Queue()
            result = _execute_monitored_queue_task(
                lambda value: bc.data_queue.put(value),
                cancel_check=lambda: True,
                args=("done",),
            )
        finally:
            bc.data_queue = previous_queue

        self.assertEqual(result, "done")

    def test_execute_monitored_queue_task_can_preserve_per_item_failure_flow(self) -> None:
        previous_queue = bc.data_queue
        fail_status = WorkerFailure()
        try:
            bc.data_queue = Queue()
            result = _execute_monitored_queue_task(
                lambda status: status.capture(RuntimeError("boom")),
                cancel_check=lambda: True,
                args=(fail_status,),
                fail_status=fail_status,
                raise_failure=False,
            )
        finally:
            bc.data_queue = previous_queue

        self.assertIsNone(result)
        self.assertTrue(fail_status.failed)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
