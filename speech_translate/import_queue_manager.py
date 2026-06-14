from __future__ import annotations

import os
from importlib import import_module
from threading import Thread
from time import gmtime, sleep, strftime, time
from typing import Any, Dict, List, Optional

from loguru import logger

from speech_translate.linker import bc
from speech_translate.utils.whisper.helper import model_keys, model_select_dict


MEDIA_FILE_TYPES = [
    "Media Files (*.wav;*.mp3;*.ogg;*.flac;*.aac;*.wma;*.m4a;*.mp4;*.mkv;*.avi;*.mov;*.webm)",
    "All Files (*.*)",
]


class ImportQueueController:
    """Owns import queue state, UI projections, and file import task orchestration."""

    def __init__(self, bridge: Any, settings: Any, headless_dialog_cls: Any, headless_mbox_fn: Any, shutdown_selenium_fn: Any):
        self.bridge = bridge
        self.settings = settings
        self.headless_dialog_cls = headless_dialog_cls
        self.headless_mbox_fn = headless_mbox_fn
        self.shutdown_selenium_fn = shutdown_selenium_fn
        self.file_import_queue: List[Any] = []
        self.processing_queue: List[Dict[str, Any]] = []
        self.batch_start_time: Optional[float] = None

    def build_import_ui(self, verify_available: bool = True) -> Dict[str, Any]:
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

    def get_import_ui_details(self) -> Dict[str, Any]:
        return self.build_import_ui(verify_available=True)

    def get_full_display_queue(self) -> List[Dict[str, Any]]:
        with self.bridge._lock:
            display_list = []
            for entry in self.file_import_queue:
                if isinstance(entry, str):
                    display_list.append({"path": entry, "name": os.path.basename(entry), "status": "", "is_completed": False})
                elif isinstance(entry, dict):
                    display_list.append(
                        {
                            "path": entry.get("path", ""),
                            "name": entry.get("name", os.path.basename(entry.get("path", ""))),
                            "status": entry.get("status", ""),
                            "is_completed": bool(entry.get("is_completed", False)),
                        }
                    )
                else:
                    try:
                        text = str(entry)
                        display_list.append({"path": text, "name": os.path.basename(text), "status": "", "is_completed": False})
                    except Exception:
                        pass

            if self.processing_queue:
                processing_map = {item.get("path"): item for item in self.processing_queue if item.get("path")}
                for item in display_list:
                    path = item.get("path")
                    if path in processing_map:
                        processing_item = processing_map[path]
                        item["status"] = str(processing_item.get("status", item.get("status", "")))
                        item["is_completed"] = bool(processing_item.get("is_completed", item.get("is_completed", False)))
            return display_list

    def get_file_processing_state(self) -> Dict[str, Any]:
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
            self.processing_queue = []
            for file_path in files:
                self.processing_queue.append(
                    {
                        "path": str(file_path),
                        "name": os.path.basename(str(file_path)),
                        "status": "Waiting",
                        "is_completed": False,
                    }
                )

        display_queue = self.get_full_display_queue()
        total = len(display_queue)
        completed_count = sum(1 for item in display_queue if item.get("is_completed", False))

        self.bridge.update_task_message(f"已准备好 {len(files)} 个待处理文件 | 队列共 {total} 个")
        self.bridge.update_task_progress(float((completed_count / total * 100) if total > 0 else 0))
        self.bridge.update_task_rows([[item.get("name", ""), item.get("status", "")] for item in display_queue])

        def _async_emit():
            try:
                self.bridge._emit_ui_update(["import"])
            except Exception:
                pass

        Thread(target=_async_emit, daemon=True).start()

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

        self.bridge.update_task_progress(float((completed_count / total * 100) if total > 0 else 0))
        self.bridge.update_task_message(message)
        self.bridge.update_task_rows([[item.get("name", ""), item.get("status", "")] for item in display_queue])

        def _async_emit():
            try:
                self.bridge._emit_ui_update(["import"])
            except Exception:
                pass

        Thread(target=_async_emit, daemon=True).start()

    def add_files_to_import_queue(self, files: Optional[list[str]] = None) -> Dict[str, Any]:
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
                if not any(
                    (isinstance(queue_item, str) and queue_item == file_path)
                    or (isinstance(queue_item, dict) and queue_item.get("path") == file_path)
                    for queue_item in self.file_import_queue
                ):
                    self.file_import_queue.append({"path": file_path, "name": os.path.basename(file_path), "status": "Waiting", "is_completed": False})
                    added += 1
        return {"ok": True, "count": len(self.file_import_queue), "added": added, "files": list(self.file_import_queue)}

    def remove_file_from_import_queue(self, index: Optional[int] = None) -> Dict[str, Any]:
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
                    if (isinstance(queue_item, str) and queue_item == path_to_remove) or (
                        isinstance(queue_item, dict) and queue_item.get("path") == path_to_remove
                    ):
                        self.file_import_queue.pop(queue_index)
                        break
            else:
                if idx < 0 or idx >= len(self.file_import_queue):
                    return {"ok": False, "message": "索引超出范围"}
                removed = self.file_import_queue.pop(idx)

        try:
            self.bridge._emit_ui_update(["import"])
        except Exception:
            pass
        return {"ok": True, "files": list(self.file_import_queue), "removed": removed}

    def clear_import_queue(self) -> Dict[str, Any]:
        with self.bridge._lock:
            self.file_import_queue = []
            self.processing_queue = []
        try:
            self.bridge._emit_ui_update(["import"])
        except Exception:
            pass
        return {"ok": True, "files": []}

    def import_files(self, files: Optional[list[str]] = None) -> Dict[str, Any]:
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
                if isinstance(entry, dict):
                    if not entry.get("is_completed", False):
                        files_to_process.append(entry.get("path", ""))
                elif isinstance(entry, str):
                    files_to_process.append(entry)
        return files_to_process

    def start_import_queue(self) -> Dict[str, Any]:
        if not self.file_import_queue:
            return {"ok": False, "message": "No files in queue"}
        if not self.bridge._wait_recording_idle(timeout_s=12.0):
            return {"ok": False, "message": "Recording is still cleaning up."}
        if self.bridge._model_load_running:
            return {"ok": False, "message": "Model loading is still in progress."}

        settings_snapshot = self.bridge.get_settings_snapshot()
        engine = self.bridge._normalize_model_key(str(settings_snapshot.get("tl_engine_f_import", "Google Translate")))
        model_name_tc = self.bridge._normalize_model_key(str(settings_snapshot.get("model_f_import", "")))
        is_tc = bool(settings_snapshot.get("transcribe_f_import", True))
        is_tl = bool(settings_snapshot.get("translate_f_import", True))

        if is_tc or (is_tl and engine in model_keys):
            if bool(self.bridge._runtime_model_loaded) and self.bridge._runtime_model_key == model_name_tc:
                self.bridge._model_load_running = False
                self.bridge._runtime_model_message = f"Model ready: {model_name_tc}"
            else:
                self.bridge._runtime_model_key = model_name_tc
                self.bridge._runtime_model_loaded = False
                self.bridge._model_load_running = True
                self.bridge._runtime_model_message = f"Loading model cache for {model_name_tc}"

        files_to_process = self._extract_files_to_process()
        if not files_to_process:
            return {"ok": False, "message": "All items are already completed"}

        self.bridge.reset_task_state("File Import")
        self.bridge.bind_headless_main_window()

        from speech_translate.utils.audio import file as audio_file_module

        audio_file_module.FileProcessDialog = lambda master, title, mode, headers: self.headless_dialog_cls(master, title, mode, headers, bridge=self.bridge)
        audio_file_module.mbox = self.headless_mbox_fn

        def worker():
            try:
                bc.enable_file_process()
                audio_file_module.process_file(
                    files_to_process,
                    model_name_tc,
                    str(settings_snapshot.get("source_lang_f_import", "English")),
                    str(settings_snapshot.get("target_lang_f_import", "Indonesian")),
                    is_tc,
                    is_tl,
                    engine,
                )
                summary = ", ".join([f"{bc.file_tced_counter} transcribed"] * is_tc + [f"{bc.file_tled_counter} translated"] * is_tl) or "no output generated"
                self.bridge.finish_task(f"File import finished: {summary}")
                if self.bridge._model_load_running:
                    self.bridge._runtime_model_loaded = True
                    self.bridge._runtime_model_message = f"Model ready: {model_name_tc}"
            except Exception as exc:
                logger.exception(exc)
                self.bridge.update_task_error(str(exc))
            finally:
                with self.bridge._lock:
                    processing_map = {item.get("path"): item for item in self.processing_queue}
                    for index, entry in enumerate(self.file_import_queue):
                        path = entry if isinstance(entry, str) else entry.get("path", "")
                        if path in processing_map:
                            processing_item = processing_map[path]
                            self.file_import_queue[index] = {
                                "path": path,
                                "name": processing_item.get("name", os.path.basename(path)),
                                "status": processing_item.get("status", ""),
                                "is_completed": bool(processing_item.get("is_completed", False)),
                            }
                    self.processing_queue = []
                bc.disable_file_process()
                self.bridge._model_load_running = False
                try:
                    self.bridge._emit_ui_update(["import"])
                except Exception:
                    pass
                if bool(settings_snapshot.get("selenium_auto_close_on_task_done", True)) and is_tl and engine == "Selenium Chrome Translate":
                    self.shutdown_selenium_fn()

        Thread(target=worker, daemon=True).start()
        return {"ok": True, "count": len(files_to_process), "message": "File import started"}

    def stop_import_queue(self) -> Dict[str, Any]:
        with self.bridge._lock:
            if not (bool(self.processing_queue) and len(self.processing_queue) > 0):
                return {"ok": False, "message": "No import is running"}
        bc.disable_file_process()
        with self.bridge._lock:
            for item in self.processing_queue:
                item["status"] = "Cancelled"
        try:
            self.bridge.update_task_message("Cancelling file import...", source="import")
        except Exception:
            pass
        try:
            self.bridge._emit_ui_update(["import"])
        except Exception:
            pass
        return {"ok": True, "message": "Cancel requested"}
