from __future__ import annotations

from time import gmtime, strftime, time
from typing import Mapping

from speech_translate.controller_protocols import JsonDict, ModelManagerControllerApi
from speech_translate.utils.whisper.helper import model_select_dict


IMPORT_ENGINE_OPTIONS = [
    "Selenium Chrome Translate",
    "Google Translate",
    "MyMemoryTranslator",
    "LibreTranslate",
] + list(model_select_dict.keys())

MODEL_DISPLAY_BY_KEY = {model_key: display_name for display_name, model_key in model_select_dict.items()}


def _resolve_model_display_name(model_key: str) -> str:
    normalized = str(model_key or "").strip()
    if not normalized:
        return ""
    return MODEL_DISPLAY_BY_KEY.get(normalized, normalized)


def count_completed_items(display_queue: list[JsonDict]) -> int:
    return sum(1 for item in display_queue if item.get("is_completed", False))


def build_import_ui_payload(
    settings_snapshot: Mapping[str, object],
    *,
    model_manager: ModelManagerControllerApi,
    source_dict_ref: Mapping[str, list[str]],
    target_dict_ref: Mapping[str, list[str]],
    verify_available: bool = True,
) -> JsonDict:
    engine = model_manager.normalize_engine_name(str(settings_snapshot.get("tl_engine_f_import", "Selenium Chrome Translate")))
    selected_model_key = model_manager.normalize_model_key(str(settings_snapshot.get("model_f_import", "")).strip())
    backend = "faster-whisper" if bool(settings_snapshot.get("use_faster_whisper", True)) else "whisper"

    available_model_options: list[JsonDict] = []
    if verify_available:
        model_dir = model_manager.resolve_model_dir()
        for display_name, model_key in model_select_dict.items():
            normalized_model_key = model_manager.normalize_model_key(model_key)
            if model_manager.is_model_available_for_backend(normalized_model_key, backend, model_dir):
                available_model_options.append({"value": normalized_model_key, "label": display_name})
        if available_model_options:
            available_model_keys = {str(option["value"]) for option in available_model_options}
            if selected_model_key not in available_model_keys:
                selected_model_key = str(available_model_options[0]["value"])
        else:
            selected_model_key = ""
    else:
        if selected_model_key:
            available_model_options = [
                {
                    "value": selected_model_key,
                    "label": _resolve_model_display_name(selected_model_key),
                }
            ]

    return {
        "backend_options": ["whisper", "faster-whisper"],
        "selected_backend": backend,
        "model_options": available_model_options,
        "selected_model": selected_model_key,
        "selected_model_key": selected_model_key,
        "selected_model_label": _resolve_model_display_name(selected_model_key),
        "engine_options": IMPORT_ENGINE_OPTIONS,
        "selected_engine": engine,
        "source_options": source_dict_ref.get(engine, source_dict_ref["Google Translate"]),
        "target_options": target_dict_ref.get(engine, target_dict_ref["Google Translate"]),
        "selected_source": settings_snapshot.get("source_lang_f_import"),
        "selected_target": settings_snapshot.get("target_lang_f_import"),
        "transcribe": settings_snapshot.get("transcribe_f_import"),
        "translate": settings_snapshot.get("translate_f_import"),
    }


def build_file_processing_state_payload(display_queue: list[JsonDict], *, active: bool) -> JsonDict:
    return {
        "ok": True,
        "files": display_queue,
        "files_total": len(display_queue),
        "files_completed": count_completed_items(display_queue),
        "active": active,
    }


def build_import_batch_ready_message(*, prepared_count: int, total_count: int) -> str:
    return f"已准备好 {prepared_count} 个待处理文件 | 队列共 {total_count} 个"


def build_import_status_message(display_queue: list[JsonDict], *, batch_start_time: float | None, time_fn=time) -> str:
    total = len(display_queue)
    completed_count = count_completed_items(display_queue)
    message = f"已完成 {completed_count}/{total} 个文件"
    if batch_start_time is not None:
        elapsed = strftime("%H:%M:%S", gmtime(time_fn() - batch_start_time))
        if elapsed:
            message += f" | 耗时: {elapsed}"
    return message


def build_task_rows(display_queue: list[JsonDict]) -> list[list[str]]:
    return [[str(item.get("name", "")), str(item.get("status", ""))] for item in display_queue]


def build_task_progress(display_queue: list[JsonDict]) -> float:
    total = len(display_queue)
    if total <= 0:
        return 0.0
    return float(count_completed_items(display_queue) / total * 100)


__all__ = [
    "IMPORT_ENGINE_OPTIONS",
    "build_file_processing_state_payload",
    "build_import_batch_ready_message",
    "build_import_status_message",
    "build_import_ui_payload",
    "build_task_progress",
    "build_task_rows",
    "count_completed_items",
]
