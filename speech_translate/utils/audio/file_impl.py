from __future__ import annotations

from threading import Thread
from time import sleep, time
from typing import Callable

from speech_translate._logging import logger
from speech_translate.runtime_deps import empty_torch_cuda_cache, get_whisper_to_language_code
from speech_translate.utils.audio.file_execution_runtime import (
    execute_monitored_queue_task as _execute_monitored_queue_task,
    run_monitored_worker as _run_monitored_worker,
)
from speech_translate.utils.audio.file_runtime_adapters import (
    FileBatchStatusContext,
    FileEnvironmentAdapter,
    FileModDependencies,
    FileModRequest,
    FileProcessDependencies,
    FileProcessRequest,
    FileProcessingStateAdapter,
    FileResultQueueAdapter,
    FileSettingsAdapter,
    FileTranslateResultDependencies,
    FileTranslateResultRequest,
    _get_file_environment,
    _get_file_settings_store,
    build_file_processing_state_adapter,
    build_file_result_queue_adapter,
    build_file_ui_bridge_adapter,
)
from speech_translate.utils.audio.file_runtime_builders import (
    _build_mod_result_runtime,
    _build_process_file_runtime,
    _build_translate_result_runtime,
    get_model,
    get_model_args,
    get_tc_args,
)
from speech_translate.utils.audio.file_workflows import (
    process_file_batch as _process_file_batch,
    run_cancellable_tc as _run_cancellable_tc,
    run_cancellable_tl as _run_cancellable_tl,
)
from speech_translate.utils.audio.file_postprocess_workflows import (
    process_mod_batch as _process_mod_batch,
    process_translate_result_batch as _process_translate_result_batch,
)

from ..helper import start_file


def _cancellable_tc(
    file_path,
    lang_source,
    lang_target,
    model_name,
    tc_func,
    tl_func,
    auto,
    is_tc,
    is_tl,
    engine,
    export_plan,
    index,
    filters,
    *,
    status_context: FileBatchStatusContext,
    processing_state: FileProcessingStateAdapter | None = None,
    result_queue: FileResultQueueAdapter | None = None,
    settings: FileSettingsAdapter,
    environment: FileEnvironmentAdapter,
    **kwargs,
):
    processing_state = processing_state or build_file_processing_state_adapter()
    result_queue = result_queue or build_file_result_queue_adapter()
    return _run_cancellable_tc(
        file_path,
        lang_source,
        lang_target,
        model_name,
        tc_func,
        tl_func,
        auto,
        is_tc,
        is_tl,
        engine,
        export_plan,
        index,
        filters,
        status_context=status_context,
        processing_state=processing_state,
        result_queue=result_queue,
        settings=settings,
        environment=environment,
        translate_target_fn=_cancellable_tl,
        thread_factory=Thread,
        **kwargs,
    )


def _cancellable_tl(
    query,
    lang_source,
    lang_target,
    tl_func,
    engine,
    export_plan,
    index,
    media_path,
    filters,
    *,
    status_context: FileBatchStatusContext,
    processing_state: FileProcessingStateAdapter | None = None,
    result_queue: FileResultQueueAdapter | None = None,
    settings: FileSettingsAdapter,
    environment: FileEnvironmentAdapter,
    **kwargs,
):
    processing_state = processing_state or build_file_processing_state_adapter()
    result_queue = result_queue or build_file_result_queue_adapter()
    return _run_cancellable_tl(
        query,
        lang_source,
        lang_target,
        tl_func,
        engine,
        export_plan,
        index,
        media_path,
        filters,
        status_context=status_context,
        processing_state=processing_state,
        result_queue=result_queue,
        settings=settings,
        environment=environment,
        **kwargs,
    )


def process_file(
    request: FileProcessRequest,
    *,
    dependencies: FileProcessDependencies | None = None,
    open_dir_fn: Callable[[str], None] = start_file,
) -> None:
    try:
        dependencies = dependencies or FileProcessDependencies(
            ui_bridge=build_file_ui_bridge_adapter(),
            result_queue=build_file_result_queue_adapter(),
            processing_state=build_file_processing_state_adapter(),
            settings=_get_file_settings_store(),
            environment=_get_file_environment(),
        )
        processing_state = dependencies.processing_state
        runtime = _build_process_file_runtime(
            request=request,
            dependencies=dependencies,
        )
        _process_file_batch(
            request,
            runtime,
            open_dir_fn=open_dir_fn,
            transcribe_target_fn=_cancellable_tc,
            translate_target_fn=_cancellable_tl,
            thread_factory=Thread,
            sleep_fn=sleep,
            time_fn=time,
        )
    except Exception as exc:
        logger.error(f"Process FILE error: {exc}")
    finally:
        processing_state.disable_file_process()
        processing_state.disable_file_tc()
        processing_state.disable_file_tl()
        empty_torch_cuda_cache()


def mod_result(
    request: FileModRequest,
    *,
    dependencies: FileModDependencies | None = None,
    open_dir_fn: Callable[[str], None] = start_file,
):
    try:
        dependencies = dependencies or FileModDependencies(
            ui_bridge=build_file_ui_bridge_adapter(),
            result_queue=build_file_result_queue_adapter(),
            processing_state=build_file_processing_state_adapter(),
            settings=_get_file_settings_store(),
        )
        processing_state = dependencies.processing_state
        runtime = _build_mod_result_runtime(
            request=request,
            dependencies=dependencies,
        )
        _process_mod_batch(
            request,
            runtime,
            open_dir_fn=open_dir_fn,
            get_transcribe_args=get_tc_args,
            resolve_language_code=lambda language: get_whisper_to_language_code().get(language, "auto"),
            execute_queue_task_fn=_execute_monitored_queue_task,
            sleep_fn=sleep,
            time_fn=time,
        )
    except Exception as exc:
        logger.error(f"Process MOD error: {exc}")
    finally:
        processing_state.disable_file_process()
        empty_torch_cuda_cache()


def translate_result(
    request: FileTranslateResultRequest,
    *,
    dependencies: FileTranslateResultDependencies | None = None,
    open_dir_fn: Callable[[str], None] = start_file,
):
    try:
        dependencies = dependencies or FileTranslateResultDependencies(
            ui_bridge=build_file_ui_bridge_adapter(),
            processing_state=build_file_processing_state_adapter(),
            settings=_get_file_settings_store(),
        )
        processing_state = dependencies.processing_state
        runtime = _build_translate_result_runtime(
            request=request,
            dependencies=dependencies,
        )
        _process_translate_result_batch(
            request,
            runtime,
            open_dir_fn=open_dir_fn,
            run_worker_fn=_run_monitored_worker,
            sleep_fn=sleep,
            time_fn=time,
        )
    except Exception as exc:
        logger.error(f"Process TL JSON error: {exc}")
    finally:
        processing_state.disable_file_process()
        empty_torch_cuda_cache()
