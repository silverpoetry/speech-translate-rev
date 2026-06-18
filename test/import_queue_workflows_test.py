from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.bridge_runtime_state import BridgeFileRuntime, BridgeRecordingRuntime, BridgeVisualRuntime
from speech_translate.import_queue_runtime import ImportQueueProcessRuntime, ImportQueueRuntimeBindings, ImportStartContext
from speech_translate.import_queue_workflows import (
    build_file_process_dependencies,
    build_file_process_request,
    build_import_summary,
    prepare_runtime_model_for_import,
)
from speech_translate.utils.whisper.helper import model_keys


class FakeModelManager:
    def __init__(self, *, loaded: bool, key: str) -> None:
        self.runtime_model_loaded = loaded
        self.runtime_model_key = key
        self.pending_calls = []
        self.ready_calls = []

    def is_runtime_model_ready(self, model_key=None):
        return bool(self.runtime_model_loaded) and (model_key is None or self.runtime_model_key == model_key)

    def mark_runtime_model_pending(self, model_key, loaded=False, message=None):
        self.pending_calls.append((model_key, loaded, message))

    def mark_runtime_model_ready(self, model_key=None, message=None):
        self.ready_calls.append((model_key, message))


class ImportQueueWorkflowTests(unittest.TestCase):
    def test_prepare_runtime_model_marks_ready_when_runtime_matches(self) -> None:
        manager = FakeModelManager(loaded=True, key="small")
        context = ImportStartContext(
            settings_snapshot={},
            engine="Google Translate",
            model_name_tc="small",
            is_tc=True,
            is_tl=False,
            files_to_process=["a.wav"],
        )

        prepare_runtime_model_for_import(context, model_manager=manager)

        self.assertEqual(manager.ready_calls, [("small", None)])
        self.assertEqual(manager.pending_calls, [])

    def test_prepare_runtime_model_marks_pending_when_runtime_differs(self) -> None:
        manager = FakeModelManager(loaded=False, key="tiny")
        context = ImportStartContext(
            settings_snapshot={},
            engine=model_keys[0],
            model_name_tc="small",
            is_tc=False,
            is_tl=True,
            files_to_process=["a.wav"],
        )

        prepare_runtime_model_for_import(context, model_manager=manager)

        self.assertEqual(manager.pending_calls, [("small", False, None)])

    def test_build_import_summary_reflects_enabled_stages(self) -> None:
        runtime = ImportQueueProcessRuntime(
            recording_state=BridgeRecordingRuntime(),
            file_state=BridgeFileRuntime(file_tced_counter=2, file_tled_counter=3),
        )

        self.assertEqual(build_import_summary(runtime, is_tc=True, is_tl=True), "2 transcribed, 3 translated")
        self.assertEqual(build_import_summary(runtime, is_tc=False, is_tl=False), "no output generated")

    def test_build_file_process_request_and_dependencies_bind_runtime_state(self) -> None:
        context = ImportStartContext(
            settings_snapshot={
                "source_lang_f_import": "English",
                "target_lang_f_import": "Chinese",
            },
            engine="Google Translate",
            model_name_tc="small",
            is_tc=True,
            is_tl=True,
            files_to_process=["a.wav"],
        )
        bindings = ImportQueueRuntimeBindings(
            recording_state=BridgeRecordingRuntime(),
            file_state=BridgeFileRuntime(),
            visual_state=BridgeVisualRuntime(has_ffmpeg=True),
        )
        bridge = object()

        request = build_file_process_request(context)
        dependencies = build_file_process_dependencies(context=context, runtime_bindings=bindings, bridge=bridge)

        self.assertEqual(request.data_files, ["a.wav"])
        self.assertEqual(request.model_name_tc, "small")
        self.assertIs(dependencies.ui_bridge.bridge, bridge)
        self.assertIs(dependencies.result_queue.state, bindings.recording_state)
        self.assertIs(dependencies.processing_state.state, bindings.file_state)
        self.assertTrue(dependencies.environment.has_ffmpeg)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
