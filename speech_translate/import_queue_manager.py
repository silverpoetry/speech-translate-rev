from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import import_module
from threading import Thread
from time import gmtime, sleep, strftime, time
from typing import Dict, List, Optional

from speech_translate.controller_protocols import (
    ImportQueueBridge,
    JsonDict,
    SettingsStore,
    ShutdownSeleniumFn,
)
from speech_translate.linker import bc
from speech_translate.log_helpers import logger
from speech_translate.ui_protocol import TASK_SOURCE_IMPORT, UI_SECTION_IMPORT
from speech_translate.utils.whisper.helper import model_keys, model_select_dict


MEDIA_FILE_TYPES = [
    "Media Files (*.wav;*.mp3;*.ogg;*.flac;*.aac;*.wma;*.m4a;*.mp4;*.mkv;*.avi;*.mov;*.webm)",
    "All Files (*.*)",
]


@dataclass
class QueueItem:
    path: str
    name: str
    status: str = ""
    is_completed: bool = False

    def to_dict(self) -> JsonDict:
        return {
            "path": self.path,
            "name": self.name,
            "status": self.status,
            "is_completed": self.is_completed,
        }


@dataclass(frozen=True)
class ImportStartContext:
    settings_snapshot: JsonDict
    engine: str
    model_name_tc: str
    is_tc: bool
    is_tl: bool
    files_to_process: list[str]

    @property
    def should_prepare_runtime_model(self) -> bool:
        return self.is_tc or (self.is_tl and self.engine in model_keys)

    @property
    def should_auto_close_selenium(self) -> bool:
        return self.is_tl and self.engine == "Selenium Chrome Translate"


class ImportQueueController:
    """Owns import queue state, UI projections, and file import task orchestration."""

    def __init__(
        self,
        bridge: ImportQueueBridge,
        settings: SettingsStore,
        shutdown_selenium_fn: ShutdownSeleniumFn,
    ):
        self.bridge = bridge
        self.settings = settings
        self.shutdown_selenium_fn = shutdown_selenium_fn
        self.file_import_queue: List[object] = []
        self.processing_queue: List[JsonDict] = []
        self.batch_start_time: Optional[float] = None

    def build_import_ui(self, verify_available: bool = True) -> JsonDict:
        settings_snapshot = dict(self.settings.cache)
        engine = self.bridge._normalize_engine_name(str(settings_snapshot.get("tl_engine_f_import", "Selenium Chrome Translate")))
        selected_model_display = str(settings_snapshot.get("model_f_import", "")).strip()
        model_name = self.bridge._normalize_model_key(selected_model_display)
        backend = "faster-whisper" if bool(settings_snapshot.get("use_faster_whisper", True)) else "whisper"

        available_model_display_names = []
        if verify_available:
            model_dir = self.bridge._resolve_model_dir()
            for display_name in list(model_select_dict.keys()):
                if self.bridge._is_model_available_for_backend(self.bridge._normalize_model_key(display_name), backend, model_dir):
                    available_model_display_names.append(display_name)
            if available_model_display_names:
                if selected_model_display not in available_model_display_names:
                    selected_model_display = available_model_display_names[0]
                model_name = self.bridge._normalize_model_key(selected_model_display)
            else:
                selected_model_display = ""
                model_name = ""
        else:
            available_model_display_names = [selected_model_display] if selected_model_display else []

        return {
            "backend_options": ["whisper", "faster-whisper"],
            "selected_backend": backend,
            "model_options": available_model_display_names,
            "selected_model": selected_model_display,
            "selected_model_key": model_name,
            "engine_options": ["Selenium Chrome Translate", "Google Translate", "MyMemoryTranslator", "LibreTranslate"] + list(model_select_dict.keys()),
            "selected_engine": engine,
            "source_options": self.bridge.TL_ENGINE_SOURCE_DICT_REF.get(engine, self.bridge.TL_ENGINE_SOURCE_DICT_REF["Google Translate"]),
            "target_options": self.bridge.TL_ENGINE_TARGET_DICT_REF.get(engine, self.bridge.TL_ENGINE_TARGET_DICT_REF["Google Translate"]),
            "selected_source": settings_snapshot.get("source_lang_f_import"),
            "selected_target": settings_snapshot.get("target_lang_f_import"),
            "transcribe": settings_snapshot.get("transcribe_f_import"),
            "translate": settings_snapshot.get("translate_f_import"),
            "queued_files": self.get_full_display_queue(),
        }

    def get_import_ui_details(self) -> JsonDict:
        return self.build_import_ui(verify_available=True)

    def get_full_display_queue(self) -> List[JsonDict]:
        with self.bridge._lock:
            display_list = [self._normalize_queue_item(entry).to_dict() for entry in self.file_import_queue]

            if self.processing_queue:
                processing_map = {item.get("path"): item for item in self.processing_queue if item.get("path")}
                for item in display_list:
                    path = item.get("path")
                    if path in processing_map:
                        processing_item = processing_map[path]
                        item["status"] = str(processing_item.get("status", item.get("status", "")))
                        item["is_completed"] = bool(processing_item.get("is_completed", item.get("is_completed", False)))
            return display_list

    def get_file_processing_state(self) -> JsonDict:
        display_queue = self.get_full_display_queue()
        return {
            "ok": True,
            "files": display_queue,
            "files_total": len(display_queue),
            "files_completed": sum(1 for item in display_queue if item.get("is_completed", False)),
            "active": bool(self.processing_queue) and bool(getattr(bc, "file_processing", False)),
        }

    def init_file_batch(self, task_name: str, files: list) -> None:
        self.batch_start_time = time()
        with self.bridge._lock:
            self.bridge.task_state.title = task_name
            self.processing_queue = [self._make_queue_item(file_path, status="Waiting").to_dict() for file_path in files]

        display_queue = self.get_full_display_queue()
        total = len(display_queue)
        self._update_task_projection(
            display_queue,
            f"已准备好 {len(files)} 个待处理文件 | 队列共 {total} 个",
            source=TASK_SOURCE_IMPORT,
        )
        self._emit_import_update(async_emit=True)

    def sync_file_status(self, index: int, combined_status: str, is_completed: bool) -> None:
        with self.bridge._lock:
            if self.processing_queue and 0 <= index < len(self.processing_queue):
                if not self.processing_queue[index].get("is_completed", False) or is_completed:
                    self.processing_queue[index]["status"] = combined_status
                    self.processing_queue[index]["is_completed"] = is_completed

        display_queue = self.get_full_display_queue()
        total = len(display_queue)
        completed_count = sum(1 for item in display_queue if item.get("is_completed", False))

        elapsed = ""
        if self.batch_start_time is not None:
            elapsed = strftime("%H:%M:%S", gmtime(time() - self.batch_start_time))

        message = f"已完成 {completed_count}/{total} 个文件"
        if elapsed:
            message += f" | 耗时: {elapsed}"

        self._update_task_projection(display_queue, message, source=TASK_SOURCE_IMPORT)
        self._emit_import_update(async_emit=True)

    def add_files_to_import_queue(self, files: Optional[list[str]] = None) -> JsonDict:
        if not self.bridge._wait_recording_idle(timeout_s=12.0):
            return {"ok": False, "message": "Recording is still cleaning up."}
        if self.bridge._model_load_running:
            return {"ok": False, "message": "Model loading is in progress."}
        if bool(self.bridge.get_recording_state().get("active", False)) or bool(bc.recording):
            return {"ok": False, "message": "Recording is active."}

        if not files:
            if not (window := self.bridge.get_window()):
                return {"ok": False, "message": "Window not ready"}
            webview = import_module("webview")
            files = window.create_file_dialog(
                getattr(getattr(webview, "FileDialog", object), "OPEN", webview.OPEN_DIALOG),
                allow_multiple=True,
                file_types=MEDIA_FILE_TYPES,
            )

        if not files:
            return {"ok": False, "message": "No files selected"}

        added = 0
        with self.bridge._lock:
            for file_path in files:
                if not any(self._normalize_queue_item(queue_item).path == file_path for queue_item in self.file_import_queue):
                    self.file_import_queue.append(self._make_queue_item(file_path, status="Waiting").to_dict())
                    added += 1
        return {"ok": True, "count": len(self.file_import_queue), "added": added, "files": list(self.file_import_queue)}

    def remove_file_from_import_queue(self, index: Optional[int] = None) -> JsonDict:
        with self.bridge._lock:
            if index is None:
                return {"ok": False, "message": "缺少索引"}
            try:
                idx = int(index)
            except Exception:
                return {"ok": False, "message": "索引无效"}

            if self.processing_queue and 0 <= idx < len(self.processing_queue):
                removed = self.processing_queue.pop(idx)
                path_to_remove = removed.get("path")
                for queue_index, queue_item in enumerate(list(self.file_import_queue)):
                    if self._normalize_queue_item(queue_item).path == path_to_remove:
                        self.file_import_queue.pop(queue_index)
                        break
            else:
                if idx < 0 or idx >= len(self.file_import_queue):
                    return {"ok": False, "message": "索引超出范围"}
                removed = self.file_import_queue.pop(idx)

        self._emit_import_update(async_emit=False)
        return {"ok": True, "files": list(self.file_import_queue), "removed": removed}

    def clear_import_queue(self) -> JsonDict:
        with self.bridge._lock:
            self.file_import_queue = []
            self.processing_queue = []
        self._emit_import_update(async_emit=False)
        return {"ok": True, "files": []}

    def import_files(self, files: Optional[list[str]] = None) -> JsonDict:
        if not files:
            if not (window := self.bridge.get_window()):
                return {"ok": False, "message": "Window not ready"}
            webview = import_module("webview")
            files = window.create_file_dialog(
                getattr(getattr(webview, "FileDialog", object), "OPEN", webview.OPEN_DIALOG),
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
        files_to_process = []
        with self.bridge._lock:
            for entry in self.file_import_queue:
                normalized = self._normalize_queue_item(entry)
                if not normalized.is_completed and normalized.path:
                    files_to_process.append(normalized.path)
        return files_to_process

    def _build_import_start_context(self) -> ImportStartContext:
        settings_snapshot = self.bridge.get_settings_snapshot()
        return ImportStartContext(
            settings_snapshot=settings_snapshot,
            engine=self.bridge._normalize_engine_name(str(settings_snapshot.get("tl_engine_f_import", "Google Translate"))),
            model_name_tc=self.bridge._normalize_model_key(str(settings_snapshot.get("model_f_import", ""))),
            is_tc=bool(settings_snapshot.get("transcribe_f_import", True)),
            is_tl=bool(settings_snapshot.get("translate_f_import", True)),
            files_to_process=self._extract_files_to_process(),
        )

    def _prepare_runtime_model_for_import(self, context: ImportStartContext) -> None:
        if not context.should_prepare_runtime_model:
            return
        if bool(self.bridge._runtime_model_loaded) and self.bridge._runtime_model_key == context.model_name_tc:
            self.bridge.model_manager_controller.mark_runtime_model_ready(context.model_name_tc)
        else:
            self.bridge.model_manager_controller.mark_runtime_model_pending(context.model_name_tc)

    def _finalize_processing_queue(self) -> None:
        with self.bridge._lock:
            processing_map = {item.get("path"): item for item in self.processing_queue}
            for index, entry in enumerate(self.file_import_queue):
                path = self._normalize_queue_item(entry).path
                if path in processing_map:
                    processing_item = processing_map[path]
                    self.file_import_queue[index] = QueueItem(
                        path=path,
                        name=processing_item.get("name", os.path.basename(path)),
                        status=processing_item.get("status", ""),
                        is_completed=bool(processing_item.get("is_completed", False)),
                    ).to_dict()
            self.processing_queue = []

    def _finish_import_run(self, *, context: ImportStartContext) -> None:
        self._finalize_processing_queue()
        bc.disable_file_process()
        self.bridge._model_load_running = False
        self._emit_import_update(async_emit=False)
        if context.should_auto_close_selenium and bool(context.settings_snapshot.get("selenium_auto_close_on_task_done", True)):
            self.shutdown_selenium_fn()

    def _build_import_summary(self, *, is_tc: bool, is_tl: bool) -> str:
        return ", ".join([f"{bc.file_tced_counter} transcribed"] * is_tc + [f"{bc.file_tled_counter} translated"] * is_tl) or "no output generated"

    def _start_import_worker(self, *, context: ImportStartContext) -> None:
        from speech_translate.utils.audio import file as audio_file_module

        def worker() -> None:
            try:
                bc.enable_file_process()
                audio_file_module.process_file(
                    context.files_to_process,
                    context.model_name_tc,
                    str(context.settings_snapshot.get("source_lang_f_import", "English")),
                    str(context.settings_snapshot.get("target_lang_f_import", "Indonesian")),
                    context.is_tc,
                    context.is_tl,
                    context.engine,
                )
                self.bridge.finish_task(
                    f"File import finished: {self._build_import_summary(is_tc=context.is_tc, is_tl=context.is_tl)}"
                )
                if self.bridge._model_load_running:
                    self.bridge.model_manager_controller.mark_runtime_model_ready(context.model_name_tc)
            except Exception as exc:
                logger.exception(exc)
                self.bridge.update_task_error(str(exc))
            finally:
                self._finish_import_run(context=context)

        Thread(target=worker, daemon=True).start()

    def start_import_queue(self) -> JsonDict:
        if not self.file_import_queue:
            return {"ok": False, "message": "No files in queue"}
        if not self.bridge._wait_recording_idle(timeout_s=12.0):
            return {"ok": False, "message": "Recording is still cleaning up."}
        if self.bridge._model_load_running:
            return {"ok": False, "message": "Model loading is still in progress."}

        context = self._build_import_start_context()
        if not context.files_to_process:
            return {"ok": False, "message": "All items are already completed"}

        self._prepare_runtime_model_for_import(context)
        self.bridge.reset_task_state("File Import")
        self.bridge.bind_headless_main_window()
        self._start_import_worker(context=context)
        return {"ok": True, "count": len(context.files_to_process), "message": "File import started"}

    def stop_import_queue(self) -> JsonDict:
        with self.bridge._lock:
            if not (bool(self.processing_queue) and len(self.processing_queue) > 0):
                return {"ok": False, "message": "No import is running"}
        bc.disable_file_process()
        with self.bridge._lock:
            for item in self.processing_queue:
                item["status"] = "Cancelled"
        try:
            self.bridge.update_task_message("Cancelling file import...", source=TASK_SOURCE_IMPORT)
        except Exception:
            pass
        self._emit_import_update(async_emit=False)
        return {"ok": True, "message": "Cancel requested"}

    def _make_queue_item(self, file_path: str, *, status: str = "", is_completed: bool = False) -> QueueItem:
        normalized_path = str(file_path)
        return QueueItem(
            path=normalized_path,
            name=os.path.basename(normalized_path),
            status=status,
            is_completed=is_completed,
        )

    def _normalize_queue_item(self, entry: object) -> QueueItem:
        if isinstance(entry, QueueItem):
            return entry
        if isinstance(entry, dict):
            path = str(entry.get("path", ""))
            return QueueItem(
                path=path,
                name=str(entry.get("name", os.path.basename(path))),
                status=str(entry.get("status", "")),
                is_completed=bool(entry.get("is_completed", False)),
            )
        text = str(entry)
        return QueueItem(path=text, name=os.path.basename(text))

    def _emit_import_update(self, *, async_emit: bool) -> None:
        def emit() -> None:
            try:
                self.bridge._emit_ui_update([UI_SECTION_IMPORT])
            except Exception:
                pass

        if async_emit:
            Thread(target=emit, daemon=True).start()
        else:
            emit()

    def _update_task_projection(self, display_queue: List[JsonDict], message: str, *, source: str = "general") -> None:
        total = len(display_queue)
        completed_count = sum(1 for item in display_queue if item.get("is_completed", False))
        progress = float((completed_count / total * 100) if total > 0 else 0)
        self.bridge.update_task_progress(progress, source=source)
        self.bridge.update_task_message(message, source=source)
        self.bridge.update_task_rows([[item.get("name", ""), item.get("status", "")] for item in display_queue])
