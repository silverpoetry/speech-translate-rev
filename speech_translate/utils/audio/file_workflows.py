from __future__ import annotations

from datetime import datetime
from threading import Thread
from time import sleep, time
from typing import Callable

from speech_translate._logging import logger
from speech_translate.utils.audio.file_batch_domain import (
    _build_base_export_name,
    _build_export_plan,
    _save_export_plan_metadata,
    _slice_display_name,
    _update_status,
)
from speech_translate.utils.audio.file_execution_runtime import (
    WorkerFailure,
    execute_monitored_queue_task,
    run_monitored_worker,
    run_translate_api,
    run_whisper,
)
from speech_translate.utils.audio.file_runtime_settings import build_file_runtime_settings
from speech_translate.utils.translate.language import get_whisper_lang_name, get_whisper_lang_similar

from ..helper import start_file
from ..whisper.helper import get_task_format, model_values
from ..whisper.result import remove_segments_by_str, split_res
from ..whisper.save import save_output_stable_ts
from ...runtime_deps import get_stable_whisper


def run_cancellable_tl(
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
    status_context,
    processing_state,
    result_queue,
    settings,
    environment,
    **kwargs,
):
    runtime_settings = build_file_runtime_settings(settings.cache)
    started_at = time()
    try:
        _update_status(status_context, "tl", index, "Translating please wait...")
        fail_status = WorkerFailure()

        format_dict = get_task_format(
            "translated",
            f"translated {lang_source} to {lang_target}",
            f"translated with {engine}",
            f"translated {lang_source} to {lang_target} with {engine}",
        )
        format_dict.update(
            get_task_format(
                "tl",
                f"tl {lang_source} to {lang_target}",
                f"tl with {engine}",
                f"tl {lang_source} to {lang_target} with {engine}",
                short_only=True,
            )
        )
        tl_export_plan = _build_export_plan(export_plan.export_dir, export_plan.base_name, format_dict)

        if engine in model_values:
            result = execute_monitored_queue_task(
                run_whisper,
                cancel_check=processing_state.is_translating_file,
                args=(tl_func, query, "translate", fail_status),
                kwargs={**kwargs, "result_queue": result_queue, "environment": environment},
                fail_status=fail_status,
                result_queue=result_queue,
            )
            if runtime_settings.filter_file_import:
                try:
                    result = remove_segments_by_str(
                        result,
                        filters.get("english", []),
                        runtime_settings.filter_file_import_case_sensitive,
                        runtime_settings.filter_file_import_strip,
                        runtime_settings.filter_file_import_ignore_punctuations,
                        runtime_settings.filter_file_import_exact_match,
                        runtime_settings.filter_file_import_similarity,
                    )
                except Exception:
                    pass
            if runtime_settings.remove_repetition_file_import:
                result = result.remove_repetition(runtime_settings.remove_repetition_amount)
        else:
            if not getattr(query, "text", "").strip():
                return _update_status(status_context, "tl", index, "TL Fail! Empty text")
            api_kwargs = (
                {"libre_link": runtime_settings.libre_link, "libre_api_key": runtime_settings.libre_api_key}
                if engine == "LibreTranslate"
                else {}
            )
            run_monitored_worker(
                run_translate_api,
                cancel_check=processing_state.is_translating_file,
                args=(query, engine, lang_source, lang_target, fail_status, settings),
                kwargs=api_kwargs,
            )
            fail_status.raise_if_failed()
            result = query

        if not getattr(result, "text", "").strip():
            return _update_status(status_context, "tl", index, "TL Fail! Empty text")

        processing_state.increment_translated_count()
        save_output_stable_ts(
            split_res(result, runtime_settings.snapshot),
            tl_export_plan.save_base_path,
            runtime_settings.export_to,
            runtime_settings,
            source_media_path=media_path,
        )
        _update_status(status_context, "tl", index, "Translated")
        _save_export_plan_metadata(export_plan, {"translate_time": time() - started_at, "translate_success": True})

    except Exception as exc:
        _update_status(status_context, "tl", index, "Failed to translate")
        if str(exc) != "Cancelled":
            logger.error(f"TL Error: {exc}")


def run_cancellable_tc(
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
    status_context,
    processing_state,
    result_queue,
    settings,
    environment,
    translate_target_fn: Callable[..., None] = run_cancellable_tl,
    thread_factory: Callable[..., Thread] = Thread,
    **kwargs,
):
    runtime_settings = build_file_runtime_settings(settings.cache)
    started_at = time()
    try:
        _update_status(status_context, "tc", index, "Transcribing please wait...")
        fail_status = WorkerFailure()

        format_dict = get_task_format(
            "transcribed",
            f"transcribed {lang_source}",
            f"transcribed with {model_name}",
            f"transcribed {lang_source} with {model_name}",
        )
        format_dict.update(
            get_task_format(
                "tc",
                f"tc {lang_source}",
                f"tc with {model_name}",
                f"tc {lang_source} with {model_name}",
                short_only=True,
            )
        )
        tc_export_plan = _build_export_plan(export_plan.export_dir, export_plan.base_name, format_dict)

        result = execute_monitored_queue_task(
            run_whisper,
            cancel_check=processing_state.is_transcribing_file,
            args=(tc_func, file_path, "transcribe", fail_status),
            kwargs={**kwargs, "result_queue": result_queue, "environment": environment},
            fail_status=fail_status,
            result_queue=result_queue,
        )
        if runtime_settings.filter_file_import:
            try:
                result = remove_segments_by_str(
                    result,
                    filters.get(get_whisper_lang_name(result.language) if auto else get_whisper_lang_similar(lang_source), []),
                    runtime_settings.filter_file_import_case_sensitive,
                    runtime_settings.filter_file_import_strip,
                    runtime_settings.filter_file_import_ignore_punctuations,
                    runtime_settings.filter_file_import_exact_match,
                    runtime_settings.filter_file_import_similarity,
                )
            except Exception:
                pass

        if runtime_settings.remove_repetition_file_import:
            result = result.remove_repetition(runtime_settings.remove_repetition_amount)

        if is_tc:
            if result.text.strip():
                processing_state.increment_transcribed_count()
                stable_whisper = get_stable_whisper()
                save_output_stable_ts(
                    split_res(stable_whisper.WhisperResult(result.to_dict()), runtime_settings.snapshot),
                    tc_export_plan.save_base_path,
                    runtime_settings.export_to,
                    runtime_settings,
                    source_media_path=file_path,
                )
            else:
                _update_status(status_context, "tc", index, "TC Fail! Got empty text")

        _update_status(status_context, "tc", index, "Transcribed")
        _save_export_plan_metadata(export_plan, {"transcribe_time": time() - started_at, "transcribe_success": True})

        if is_tl:
            tl_query = file_path if engine in model_values else result
            thread_factory(
                target=translate_target_fn,
                args=[tl_query, lang_source, lang_target, tl_func, engine, export_plan, index, file_path, filters],
                kwargs={
                    **kwargs,
                    "status_context": status_context,
                    "processing_state": processing_state,
                    "result_queue": result_queue,
                    "settings": settings,
                    "environment": environment,
                },
                daemon=True,
            ).start()

    except Exception as exc:
        _update_status(status_context, "tc", index, "Failed to transcribe")
        if is_tl:
            _update_status(status_context, "tl", index, "Skipped (TC Failed)")
        if str(exc) != "Cancelled":
            logger.error(f"TC Error: {exc}")


def process_file_batch(
    request,
    runtime,
    *,
    open_dir_fn: Callable[[str], None] = start_file,
    transcribe_target_fn: Callable[..., None] = run_cancellable_tc,
    translate_target_fn: Callable[..., None] = run_cancellable_tl,
    thread_factory: Callable[..., Thread] = Thread,
    sleep_fn: Callable[[float], None] = sleep,
    time_fn: Callable[[], float] = time,
) -> None:
    status_context = runtime.status_context
    processing_state = runtime.processing_state
    processing_state.reset_file_counts()
    processing_state.enable_file_tc()
    processing_state.enable_file_tl()

    runtime.ui_bridge.init_file_batch(
        f"Task: {runtime.taskname} with {request.model_name_tc}",
        request.data_files,
    )

    def is_still_active():
        return status_context.has_active_work(len(request.data_files))

    for index, file_path in enumerate(request.data_files):
        if not processing_state.is_file_processing():
            break
        logger.info(f"Loop entered for file: {file_path}")
        file_name = _slice_display_name(file_path, start=runtime.slice_start, end=runtime.slice_end)
        base_name = _build_base_export_name(
            datetime.now().strftime(runtime.runtime_settings.export_format),
            file_name,
            request.lang_source,
            request.lang_target,
            request.model_name_tc,
            request.engine,
        )
        export_plan = _build_export_plan(runtime.export_dir, base_name, {})
        _save_export_plan_metadata(
            export_plan,
            {
                "meta_written_at": str(datetime.now()),
                "task": runtime.taskname,
                "filename": file_name,
                "transcribe": request.is_tc,
                "translate": request.is_tl,
                "model": request.model_name_tc,
                "engine": request.engine,
            },
        )

        if request.is_tl and not request.is_tc and runtime.tl_engine_whisper:
            thread_factory(
                target=translate_target_fn,
                args=[file_path, request.lang_source, request.lang_target, runtime.stable_tl, request.engine, export_plan, index, file_path, runtime.filters],
                kwargs={
                    **runtime.whisper_args,
                    "status_context": status_context,
                    "processing_state": processing_state,
                    "result_queue": runtime.result_queue,
                    "settings": runtime.settings,
                    "environment": runtime.environment,
                },
                daemon=True,
            ).start()
        else:
            tc_thread = thread_factory(
                target=transcribe_target_fn,
                args=[
                    file_path,
                    request.lang_source,
                    request.lang_target,
                    request.model_name_tc,
                    runtime.stable_tc,
                    runtime.stable_tl,
                    request.lang_source == "auto detect",
                    request.is_tc,
                    request.is_tl,
                    request.engine,
                    export_plan,
                    index,
                    runtime.filters,
                ],
                kwargs={
                    **runtime.whisper_args,
                    "status_context": status_context,
                    "processing_state": processing_state,
                    "result_queue": runtime.result_queue,
                    "settings": runtime.settings,
                    "environment": runtime.environment,
                    "translate_target_fn": translate_target_fn,
                    "thread_factory": thread_factory,
                },
                daemon=True,
            )
            tc_thread.start()
            tc_thread.join()

    while processing_state.is_file_processing() and is_still_active():
        sleep_fn(0.5)

    logger.info(f"Process FILE completed in {time_fn() - runtime.started_at:.2f}s")
    if (processing_state.transcribed_count() > 0 or processing_state.translated_count() > 0) and runtime.runtime_settings.auto_open_dir_export:
        open_dir_fn(runtime.export_dir)


__all__ = [
    "process_file_batch",
    "run_cancellable_tc",
    "run_cancellable_tl",
]
