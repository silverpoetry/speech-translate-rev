from __future__ import annotations

from threading import Thread
from time import time
from typing import List, Optional

from speech_translate.controller_protocols import (
    ImportQueueBridge,
    JsonDict,
    ModelManagerControllerApi,
    RecordingControllerApi,
    SettingsStore,
    ShutdownSeleniumFn,
)
from speech_translate.import_queue_state import ImportQueueStateStore, QueueItem
from speech_translate.import_queue_runtime import (
    ImportQueueProcessRuntime,
    ImportQueueRuntimeBindings,
    ImportStartContext,
    build_import_start_context,
)
from speech_translate.import_queue_view import (
    build_file_processing_state_payload,
    build_import_batch_ready_message,
    build_import_status_message,
    build_import_ui_payload,
    build_task_progress,
    build_task_rows,
)
from speech_translate.import_queue_workflows import (
    build_file_process_dependencies,
    build_file_process_request,
    build_import_summary,
    prepare_runtime_model_for_import,
)
from speech_translate.log_helpers import logger
from speech_translate.ui_protocol import TASK_SOURCE_IMPORT, UI_SECTION_IMPORT
from speech_translate.webview_runtime import create_file_dialog


MEDIA_FILE_TYPES = [
    "Media Files (*.wav;*.mp3;*.ogg;*.flac;*.aac;*.wma;*.m4a;*.mp4;*.mkv;*.avi;*.mov;*.webm)",
    "All Files (*.*)",
]


class ImportQueueController:
    """Owns import queue state, UI projections, and file import task orchestration."""

    def __init__(
        self,
        bridge: ImportQueueBridge,
        settings: SettingsStore,
        shutdown_selenium_fn: ShutdownSeleniumFn,
        recording_controller: RecordingControllerApi,
        model_manager: ModelManagerControllerApi,
        runtime_bindings: ImportQueueRuntimeBindings,
        process_runtime: ImportQueueProcessRuntime | None = None,
    ):
        self.bridge = bridge
        self.settings = settings
        self.shutdown_selenium_fn = shutdown_selenium_fn
        self.recording_controller = recording_controller
        self.model_manager = model_manager
        self.runtime_bindings = runtime_bindings
        self.process_runtime = process_runtime or runtime_bindings.build_process_runtime()
        self.queue_state = ImportQueueStateStore()
        self.batch_start_time: Optional[float] = None

    @property
    def file_import_queue(self) -> List[object]:
        return self.queue_state.file_import_queue

    @file_import_queue.setter
    def file_import_queue(self, value: List[object]) -> None:
        self.queue_state.file_import_queue = list(value)

    @property
    def processing_queue(self) -> List[JsonDict]:
        return self.queue_state.processing_queue

    @processing_queue.setter
    def processing_queue(self, value: List[JsonDict]) -> None:
        self.queue_state.processing_queue = list(value)

    def build_import_ui(self, verify_available: bool = True) -> JsonDict:
        payload = build_import_ui_payload(
            dict(self.settings.cache),
            model_manager=self.model_manager,
            source_dict_ref=self.bridge.TL_ENGINE_SOURCE_DICT_REF,
            target_dict_ref=self.bridge.TL_ENGINE_TARGET_DICT_REF,
            verify_available=verify_available,
        )
        payload["queued_files"] = self.get_full_display_queue()
        return payload

    def get_import_ui_details(self) -> JsonDict:
        return self.build_import_ui(verify_available=True)

    def get_full_display_queue(self) -> List[JsonDict]:
        return self.queue_state.get_display_queue()

    def get_file_processing_state(self) -> JsonDict:
        display_queue = self.get_full_display_queue()
        return build_file_processing_state_payload(
            display_queue,
            active=bool(self.processing_queue) and self.process_runtime.is_file_processing_active(),
        )

    def init_file_batch(self, task_name: str, files: list) -> None:
        self.batch_start_time = time()
        self.bridge.set_task_title(task_name)
        self.queue_state.set_processing_batch(files)

        display_queue = self.get_full_display_queue()
        total = len(display_queue)
        self._update_task_projection(
            display_queue,
            build_import_batch_ready_message(prepared_count=len(files), total_count=total),
            source=TASK_SOURCE_IMPORT,
        )
        self._emit_import_update(async_emit=True)

    def sync_file_status(self, index: int, combined_status: str, is_completed: bool) -> None:
        self.queue_state.sync_processing_status(index, combined_status, is_completed)

        display_queue = self.get_full_display_queue()
        self._update_task_projection(
            display_queue,
            build_import_status_message(display_queue, batch_start_time=self.batch_start_time, time_fn=time),
            source=TASK_SOURCE_IMPORT,
        )
        self._emit_import_update(async_emit=True)

    def add_files_to_import_queue(self, files: Optional[list[str]] = None) -> JsonDict:
        if not self.recording_controller.wait_recording_idle(timeout_s=12.0):
            return {"ok": False, "message": "Recording is still cleaning up."}
        if self._is_model_load_running():
            return {"ok": False, "message": "Model loading is in progress."}
        if bool(self.recording_controller.get_recording_state().get("active", False)) or self.process_runtime.is_recording_active():
            return {"ok": False, "message": "Recording is active."}

        if not files:
            if not (window := self.bridge.get_window()):
                return {"ok": False, "message": "Window not ready"}
            files = create_file_dialog(
                window,
                dialog_kind="open",
                allow_multiple=True,
                file_types=MEDIA_FILE_TYPES,
            )

        if not files:
            return {"ok": False, "message": "No files selected"}

        added = self.queue_state.add_files(files)
        return {"ok": True, "count": len(self.file_import_queue), "added": added, "files": list(self.file_import_queue)}

    def remove_file_from_import_queue(self, index: Optional[int] = None) -> JsonDict:
        if index is None:
            return {"ok": False, "message": "缺少索引"}
        try:
            idx = int(index)
        except Exception:
            return {"ok": False, "message": "索引无效"}

        removed = self.queue_state.remove_by_index(idx)
        if removed is None:
            return {"ok": False, "message": "索引超出范围"}

        self._emit_import_update(async_emit=False)
        return {"ok": True, "files": list(self.file_import_queue), "removed": removed}

    def clear_import_queue(self) -> JsonDict:
        self.queue_state.clear()
        self._emit_import_update(async_emit=False)
        return {"ok": True, "files": []}

    def import_files(self, files: Optional[list[str]] = None) -> JsonDict:
        if not files:
            if not (window := self.bridge.get_window()):
                return {"ok": False, "message": "Window not ready"}
            files = create_file_dialog(
                window,
                dialog_kind="open",
                allow_multiple=True,
                file_types=MEDIA_FILE_TYPES,
            )
        if not files:
            return {"ok": False, "message": "No files selected"}

        result = self.add_files_to_import_queue(files)
        if not result.get("ok"):
            return result
        return self.start_import_queue()

    def _extract_files_to_process(self) -> list[str]:
        return self.queue_state.extract_files_to_process()

    def _build_import_start_context(self) -> ImportStartContext:
        return build_import_start_context(
            self.bridge.get_settings_snapshot(),
            normalize_engine_name=self.model_manager.normalize_engine_name,
            normalize_model_key=self.model_manager.normalize_model_key,
            files_to_process=self._extract_files_to_process(),
        )

    def _prepare_runtime_model_for_import(self, context: ImportStartContext) -> None:
        prepare_runtime_model_for_import(context, model_manager=self.model_manager)

    def _finalize_processing_queue(self) -> None:
        self.queue_state.finalize_processing_queue()

    def _finish_import_run(self, *, context: ImportStartContext) -> None:
        self._finalize_processing_queue()
        self.process_runtime.disable_file_processing()
        self._set_model_load_running(False)
        self._emit_import_update(async_emit=False)
        if context.should_auto_close_selenium and bool(context.settings_snapshot.get("selenium_auto_close_on_task_done", True)):
            self.shutdown_selenium_fn()

    def _build_import_summary(self, *, is_tc: bool, is_tl: bool) -> str:
        return build_import_summary(self.process_runtime, is_tc=is_tc, is_tl=is_tl)

    def _build_file_process_dependencies(self, *, context: ImportStartContext):
        return build_file_process_dependencies(
            context=context,
            runtime_bindings=self.runtime_bindings,
            bridge=self,
        )

    def _start_import_worker(self, *, context: ImportStartContext) -> None:
        from speech_translate.utils.audio import file_api as audio_file_module
        dependencies = self._build_file_process_dependencies(context=context)

        def worker() -> None:
            try:
                self.process_runtime.enable_file_processing()
                audio_file_module.process_file(
                    build_file_process_request(context),
                    dependencies=dependencies,
                )
                self.bridge.finish_task(
                    f"File import finished: {self._build_import_summary(is_tc=context.is_tc, is_tl=context.is_tl)}"
                )
                if self._is_model_load_running():
                    self.model_manager.mark_runtime_model_ready(context.model_name_tc)
            except Exception as exc:
                logger.exception(exc)
                self.bridge.update_task_error(str(exc))
            finally:
                self._finish_import_run(context=context)

        Thread(target=worker, daemon=True).start()

    def start_import_queue(self) -> JsonDict:
        if not self.file_import_queue:
            return {"ok": False, "message": "No files in queue"}
        if not self.recording_controller.wait_recording_idle(timeout_s=12.0):
            return {"ok": False, "message": "Recording is still cleaning up."}
        if self._is_model_load_running():
            return {"ok": False, "message": "Model loading is still in progress."}

        context = self._build_import_start_context()
        if not context.files_to_process:
            return {"ok": False, "message": "All items are already completed"}

        self._prepare_runtime_model_for_import(context)
        self.bridge.reset_task_state("File Import")
        self._start_import_worker(context=context)
        return {"ok": True, "count": len(context.files_to_process), "message": "File import started"}

    def stop_import_queue(self) -> JsonDict:
        if not self.processing_queue:
            return {"ok": False, "message": "No import is running"}
        self.process_runtime.disable_file_processing()
        self.queue_state.cancel_processing(status="Cancelled")
        try:
            self.bridge.update_task_message("Cancelling file import...", source=TASK_SOURCE_IMPORT)
        except Exception:
            pass
        self._emit_import_update(async_emit=False)
        return {"ok": True, "message": "Cancel requested"}

    def _make_queue_item(self, file_path: str, *, status: str = "", is_completed: bool = False) -> QueueItem:
        return self.queue_state.make_queue_item(file_path, status=status, is_completed=is_completed)

    def _normalize_queue_item(self, entry: object) -> QueueItem:
        return self.queue_state.normalize_queue_item(entry)

    def _emit_import_update(self, *, async_emit: bool) -> None:
        def emit() -> None:
            try:
                self.bridge.emit_ui_update([UI_SECTION_IMPORT])
            except Exception:
                pass

        if async_emit:
            Thread(target=emit, daemon=True).start()
        else:
            emit()

    def _update_task_projection(self, display_queue: List[JsonDict], message: str, *, source: str = "general") -> None:
        self.bridge.update_task_progress(build_task_progress(display_queue), source=source)
        self.bridge.update_task_message(message, source=source)
        self.bridge.update_task_rows(build_task_rows(display_queue))

    def _is_model_load_running(self) -> bool:
        return bool(self.model_manager.is_runtime_model_loading())

    def _set_model_load_running(self, loading: bool) -> None:
        self.model_manager.set_runtime_model_loading(bool(loading))
