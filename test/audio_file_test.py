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
    FileProcessRuntime,
    FileProcessingStateAdapter,
    FileResultQueueAdapter,
    FileUiBridgeAdapter,
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
    process_file,
)
class FakeFileStatusBridge:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.calls = []
        self.batches = []

    def init_file_batch(self, task_name: str, files) -> None:
        self.batches.append((task_name, list(files)))

    def sync_file_status(self, index: int, status: str, is_completed: bool) -> None:
        self.calls.append((index, status, is_completed))
        if self.should_fail:
            raise RuntimeError("bridge failed")


class FakeResultQueueAdapter:
    def __init__(self) -> None:
        self.values = Queue()

    def get(self):
        return self.values.get()

    def put(self, payload) -> None:
        self.values.put(payload)


class FakeProcessingStateAdapter:
    def __init__(self, *, file_processing: bool = True) -> None:
        self.file_processing = file_processing
        self.transcribing_file = True
        self.translating_file = True
        self.tc_enabled = 0
        self.tl_enabled = 0
        self.tc_disabled = 0
        self.tl_disabled = 0
        self.process_disabled = 0
        self.tc_count = 0
        self.tl_count = 0
        self.mod_count = 0

    def is_file_processing(self) -> bool:
        return self.file_processing

    def is_transcribing_file(self) -> bool:
        return self.transcribing_file

    def is_translating_file(self) -> bool:
        return self.translating_file

    def reset_file_counts(self) -> None:
        self.tc_count = 0
        self.tl_count = 0

    def increment_transcribed_count(self) -> None:
        self.tc_count += 1

    def increment_translated_count(self) -> None:
        self.tl_count += 1

    def transcribed_count(self) -> int:
        return self.tc_count

    def translated_count(self) -> int:
        return self.tl_count

    def enable_file_tc(self) -> None:
        self.tc_enabled += 1

    def enable_file_tl(self) -> None:
        self.tl_enabled += 1

    def disable_file_tc(self) -> None:
        self.tc_disabled += 1

    def disable_file_tl(self) -> None:
        self.tl_disabled += 1

    def disable_file_process(self) -> None:
        self.process_disabled += 1

    def reset_mod_counter(self) -> None:
        self.mod_count = 0

    def increment_mod_counter(self) -> None:
        self.mod_count += 1

    def mod_counter(self) -> int:
        return self.mod_count


class AudioFileHelpersTests(unittest.TestCase):
    def test_build_process_file_runtime_collects_shared_runtime_state(self) -> None:
        fake_stable_tc = object()
        fake_stable_tl = object()
        bridge_adapter = FileUiBridgeAdapter(FakeFileStatusBridge())
        result_queue = FakeResultQueueAdapter()
        processing_state = FakeProcessingStateAdapter()
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
                ui_bridge=bridge_adapter,
                result_queue=result_queue,
                processing_state=processing_state,
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
        self.assertIs(runtime.ui_bridge, bridge_adapter)
        self.assertIs(runtime.result_queue, result_queue)
        self.assertIs(runtime.processing_state, processing_state)

    def test_build_mod_result_runtime_selects_mode_specific_dependencies(self) -> None:
        fake_model = Mock()
        fake_model.refine = Mock(name="refine")
        fake_model.align = Mock(name="align")
        fake_stable_whisper = Mock()
        fake_stable_whisper.load_model.return_value = fake_model
        bridge_adapter = FileUiBridgeAdapter(FakeFileStatusBridge())
        result_queue = FakeResultQueueAdapter()
        processing_state = FakeProcessingStateAdapter()
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
                ui_bridge=bridge_adapter,
                result_queue=result_queue,
                processing_state=processing_state,
            )

        self.assertEqual(runtime.action, "Alignment")
        self.assertTrue(os.path.normpath(runtime.export_dir).endswith("@aligned"))
        self.assertEqual((runtime.slice_start, runtime.slice_end), (None, 6))
        self.assertIs(runtime.stable_whisper_api, fake_stable_whisper)
        self.assertIs(runtime.model, fake_model)
        self.assertIs(runtime.mod_func, fake_model.align)
        self.assertEqual(runtime.mod_args, {"steps": 2})
        self.assertEqual(runtime.started_at, 84.0)
        self.assertIs(runtime.ui_bridge, bridge_adapter)
        self.assertIs(runtime.result_queue, result_queue)
        self.assertIs(runtime.processing_state, processing_state)

    def test_build_translate_result_runtime_scopes_engine_specific_api_kwargs(self) -> None:
        fake_stable_whisper = object()
        bridge_adapter = FileUiBridgeAdapter(FakeFileStatusBridge())
        processing_state = FakeProcessingStateAdapter()
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
                ui_bridge=bridge_adapter,
                processing_state=processing_state,
            )

        self.assertEqual(os.path.normpath(runtime.export_dir), os.path.normpath("D:\\exports\\@translated"))
        self.assertEqual((runtime.slice_start, runtime.slice_end), (2, None))
        self.assertIs(runtime.stable_whisper_api, fake_stable_whisper)
        self.assertEqual(
            runtime.api_kwargs,
            {"libre_link": "http://127.0.0.1:5000", "libre_api_key": "secret"},
        )
        self.assertEqual(runtime.started_at, 126.0)
        self.assertIs(runtime.ui_bridge, bridge_adapter)
        self.assertIs(runtime.processing_state, processing_state)

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
        bridge = FakeFileStatusBridge()
        context = FileBatchStatusContext(is_tc=True, is_tl=True, ui_bridge=FileUiBridgeAdapter(bridge))
        context.update_status("tc", 0, "Transcribed")
        context.update_status("tl", 0, "Translated")

        self.assertEqual(context.tc_status[0], "Transcribed")
        self.assertEqual(context.tl_status[0], "Translated")
        self.assertEqual(bridge.calls[-1], (0, "Transcribed, Translated", True))

    def test_file_batch_status_context_suppresses_bridge_sync_errors(self) -> None:
        bridge = FakeFileStatusBridge(should_fail=True)
        context = FileBatchStatusContext(is_mod=True, ui_bridge=FileUiBridgeAdapter(bridge))
        context.update_status("mod", 3, "Processing")

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
        result_queue = FakeResultQueueAdapter()
        result = _execute_monitored_queue_task(
            lambda value: result_queue.put(value),
            cancel_check=lambda: True,
            args=("done",),
            result_queue=result_queue,
        )

        self.assertEqual(result, "done")

    def test_execute_monitored_queue_task_can_preserve_per_item_failure_flow(self) -> None:
        fail_status = WorkerFailure()
        result_queue = FakeResultQueueAdapter()
        result = _execute_monitored_queue_task(
            lambda status: status.capture(RuntimeError("boom")),
            cancel_check=lambda: True,
            args=(fail_status,),
            fail_status=fail_status,
            raise_failure=False,
            result_queue=result_queue,
        )

        self.assertIsNone(result)
        self.assertTrue(fail_status.failed)

    def test_process_file_supports_injected_runtime_adapters(self) -> None:
        bridge = FakeFileStatusBridge()
        ui_bridge = FileUiBridgeAdapter(bridge)
        result_queue = FakeResultQueueAdapter()
        processing_state = FakeProcessingStateAdapter(file_processing=False)
        opened = []
        runtime = FileProcessRuntime(
            status_context=FileBatchStatusContext(is_tc=True, ui_bridge=ui_bridge),
            export_dir="D:\\exports",
            slice_start=None,
            slice_end=None,
            tl_engine_whisper=False,
            stable_tc=object(),
            stable_tl=object(),
            whisper_args={},
            filters={},
            taskname="Transcribe",
            started_at=1.0,
            ui_bridge=ui_bridge,
            result_queue=result_queue,
            processing_state=processing_state,
        )
        with (
            patch("speech_translate.utils.audio.file._build_process_file_runtime", return_value=runtime),
            patch("speech_translate.utils.audio.file.empty_torch_cuda_cache"),
            patch("speech_translate.utils.audio.file.time", return_value=1.0),
            patch.dict("speech_translate.utils.audio.file.sj.cache", {"auto_open_dir_export": True}, clear=False),
        ):
            process_file(
                ["a.wav"],
                "small",
                "English",
                "Chinese",
                True,
                False,
                "Google Translate",
                ui_bridge=ui_bridge,
                result_queue=result_queue,
                processing_state=processing_state,
                open_dir_fn=lambda target: opened.append(target),
            )

        self.assertEqual(bridge.batches, [("Task: Transcribe with small", ["a.wav"])])
        self.assertEqual(processing_state.tc_enabled, 1)
        self.assertEqual(processing_state.tl_enabled, 1)
        self.assertEqual(processing_state.process_disabled, 1)
        self.assertEqual(processing_state.tc_disabled, 1)
        self.assertEqual(processing_state.tl_disabled, 1)
        self.assertEqual(opened, [])


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
