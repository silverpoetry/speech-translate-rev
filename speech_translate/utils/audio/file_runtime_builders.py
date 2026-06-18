from __future__ import annotations

from time import time
from typing import Mapping

from speech_translate._path import dir_alignment, dir_export, dir_refinement, dir_translate
from speech_translate.runtime_deps import get_stable_whisper, get_whisper_to_language_code
from speech_translate.utils.audio.file_batch_domain import FileBatchStatusContext, _resolve_slice_bounds
from speech_translate.utils.audio.file_runtime_adapters import (
    FileModDependencies,
    FileModRequest,
    FileModRuntime,
    FileProcessDependencies,
    FileProcessRequest,
    FileProcessRuntime,
    FileResultTranslateRuntime,
    FileSettingsAdapter,
    FileTranslateResultDependencies,
    FileTranslateResultRequest,
)
from speech_translate.utils.audio.file_runtime_settings import build_file_runtime_settings
from speech_translate.utils.translate.language import get_whisper_lang_similar

from ..whisper.helper import get_hallucination_filter, model_values


def _get_whisper_runtime_api():
    from speech_translate.utils.whisper import load as whisper_load_api

    return whisper_load_api


def get_model(*args, **kwargs):
    return _get_whisper_runtime_api().get_model(*args, **kwargs)


def get_model_args(*args, **kwargs):
    return _get_whisper_runtime_api().get_model_args(*args, **kwargs)


def get_tc_args(*args, **kwargs):
    return _get_whisper_runtime_api().get_tc_args(*args, **kwargs)


def _resolve_process_export_dir(setting_cache: Mapping[str, object]) -> str:
    return dir_export if setting_cache["dir_export"] == "auto" else str(setting_cache["dir_export"])


def _resolve_mod_export_dir(setting_cache: Mapping[str, object], *, action: str) -> str:
    if setting_cache["dir_export"] == "auto":
        return dir_refinement if action == "Refinement" else dir_alignment
    return str(setting_cache["dir_export"]) + f"/@{action.lower()}"


def _resolve_translate_result_export_dir(setting_cache: Mapping[str, object]) -> str:
    if setting_cache["dir_export"] == "auto":
        return dir_translate
    return str(setting_cache["dir_export"]) + "/@translated"


def _build_process_file_runtime(
    *,
    request: FileProcessRequest,
    dependencies: FileProcessDependencies,
) -> FileProcessRuntime:
    ui_bridge = dependencies.ui_bridge
    result_queue = dependencies.result_queue
    processing_state = dependencies.processing_state
    settings = dependencies.settings
    environment = dependencies.environment
    runtime_settings = build_file_runtime_settings(settings.cache)
    tl_engine_whisper = request.engine in model_values
    _, _, stable_tc, stable_tl, to_args = get_model(
        request.is_tc,
        request.is_tl,
        tl_engine_whisper,
        request.model_name_tc,
        request.engine,
        runtime_settings.snapshot,
        **get_model_args(runtime_settings.snapshot),
    )
    whisper_args = get_tc_args(to_args, runtime_settings.snapshot)
    whisper_args["language"] = (
        get_whisper_to_language_code()[get_whisper_lang_similar(request.lang_source)]
        if request.lang_source != "auto detect"
        else None
    )
    whisper_args["verbose"] = None
    taskname = (
        "Transcribe & Translate"
        if request.is_tc and request.is_tl
        else "Transcribe"
        if request.is_tc
        else "Translate"
    )
    filters = (
        get_hallucination_filter("file", runtime_settings.path_filter_file_import)
        if runtime_settings.filter_file_import
        else {}
    )
    slice_start, slice_end = _resolve_slice_bounds(runtime_settings.snapshot)
    return FileProcessRuntime(
        status_context=FileBatchStatusContext(
            is_tc=request.is_tc,
            is_tl=request.is_tl,
            is_mod=False,
            ui_bridge=ui_bridge,
        ),
        export_dir=_resolve_process_export_dir(runtime_settings.snapshot),
        slice_start=slice_start,
        slice_end=slice_end,
        tl_engine_whisper=tl_engine_whisper,
        stable_tc=stable_tc,
        stable_tl=stable_tl,
        whisper_args=whisper_args,
        filters=filters,
        taskname=taskname,
        started_at=time(),
        ui_bridge=ui_bridge,
        result_queue=result_queue,
        processing_state=processing_state,
        settings=settings,
        runtime_settings=runtime_settings,
        environment=environment,
    )


def _build_mod_result_runtime(
    *,
    request: FileModRequest,
    dependencies: FileModDependencies,
) -> FileModRuntime:
    ui_bridge = dependencies.ui_bridge
    result_queue = dependencies.result_queue
    processing_state = dependencies.processing_state
    settings = dependencies.settings
    runtime_settings = build_file_runtime_settings(settings.cache)
    action = "Refinement" if request.mode == "refinement" else "Alignment"
    stable_whisper = get_stable_whisper()
    model = stable_whisper.load_model(request.model_name_tc, **get_model_args(runtime_settings.snapshot))
    mod_func = model.refine if request.mode == "refinement" else model.align
    slice_start, slice_end = _resolve_slice_bounds(runtime_settings.snapshot)
    return FileModRuntime(
        status_context=FileBatchStatusContext(is_tc=False, is_tl=False, is_mod=True, ui_bridge=ui_bridge),
        action=action,
        export_dir=_resolve_mod_export_dir(runtime_settings.snapshot, action=action),
        slice_start=slice_start,
        slice_end=slice_end,
        stable_whisper_api=stable_whisper,
        model=model,
        mod_func=mod_func,
        mod_args=get_tc_args(
            mod_func,
            runtime_settings.snapshot,
            mode="refine" if request.mode == "refinement" else "align",
        ),
        started_at=time(),
        ui_bridge=ui_bridge,
        result_queue=result_queue,
        processing_state=processing_state,
        settings=settings,
        runtime_settings=runtime_settings,
    )


def _build_translate_result_runtime(
    *,
    request: FileTranslateResultRequest,
    dependencies: FileTranslateResultDependencies,
) -> FileResultTranslateRuntime:
    ui_bridge = dependencies.ui_bridge
    processing_state = dependencies.processing_state
    settings = dependencies.settings
    runtime_settings = build_file_runtime_settings(settings.cache)
    slice_start, slice_end = _resolve_slice_bounds(runtime_settings.snapshot)
    api_kwargs = (
        {"libre_link": runtime_settings.libre_link, "libre_api_key": runtime_settings.libre_api_key}
        if request.engine == "LibreTranslate"
        else {}
    )
    return FileResultTranslateRuntime(
        status_context=FileBatchStatusContext(is_tc=False, is_tl=False, is_mod=True, ui_bridge=ui_bridge),
        export_dir=_resolve_translate_result_export_dir(runtime_settings.snapshot),
        slice_start=slice_start,
        slice_end=slice_end,
        stable_whisper_api=get_stable_whisper(),
        api_kwargs=api_kwargs,
        started_at=time(),
        ui_bridge=ui_bridge,
        processing_state=processing_state,
        settings=settings,
        runtime_settings=runtime_settings,
    )

