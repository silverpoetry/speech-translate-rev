from __future__ import annotations

from datetime import datetime
from time import sleep, time
from typing import Callable

from speech_translate._logging import logger
from speech_translate.utils.audio.file_batch_domain import (
    _build_base_export_name,
    _build_export_plan,
    _resolve_alignment_language,
    _save_export_plan_metadata,
    _slice_display_name,
    _update_status,
)
from speech_translate.utils.audio.file_execution_runtime import (
    WorkerFailure,
    execute_monitored_queue_task,
    run_monitored_worker,
    run_translate_api,
)

from ..helper import start_file
from ..whisper.helper import get_task_format, to_language_name
from ..whisper.result import split_res
from ..whisper.save import save_output_stable_ts


def process_mod_batch(
    request,
    runtime,
    *,
    open_dir_fn: Callable[[str], None] = start_file,
    get_transcribe_args: Callable[[object, object], dict[str, object]],
    resolve_language_code: Callable[[str], str],
    execute_queue_task_fn: Callable[..., object] = execute_monitored_queue_task,
    sleep_fn: Callable[[float], None] = sleep,
    time_fn: Callable[[], float] = time,
) -> None:
    status_context = runtime.status_context
    processing_state = runtime.processing_state
    processing_state.reset_mod_counter()

    runtime.ui_bridge.init_file_batch(
        f"Task {request.mode} with {request.model_name_tc}",
        [file_data[0] for file_data in request.data_files],
    )

    def is_still_active():
        return status_context.has_active_work(len(request.data_files))

    task_short = {"refinement": "rf", "alignment": "al"}

    for index, file_data in enumerate(request.data_files):
        if not processing_state.is_file_processing():
            break

        audio_path, mod_path = file_data[0], file_data[1]
        file_name = _slice_display_name(audio_path, start=runtime.slice_start, end=runtime.slice_end)
        base_name = _build_base_export_name(
            datetime.now().strftime(runtime.runtime_settings.export_format),
            file_name,
            "",
            "",
            request.model_name_tc,
            "",
        )
        format_dict = get_task_format(
            runtime.action,
            runtime.action,
            f"{runtime.action} with {request.model_name_tc}",
            f"{runtime.action} with {request.model_name_tc}",
        )
        format_dict.update(
            get_task_format(
                task_short[request.mode],
                task_short[request.mode],
                f"{task_short[request.mode]} with {request.model_name_tc}",
                f"{task_short[request.mode]} with {request.model_name_tc}",
                short_only=True,
            )
        )
        export_plan = _build_export_plan(runtime.export_dir, base_name, format_dict)

        try:
            mod_src = runtime.stable_whisper_api.WhisperResult(mod_path) if mod_path.endswith(".json") else open(mod_path, "r", encoding="utf-8").read()
        except Exception:
            _update_status(status_context, "mod", index, "Parse Error")
            continue

        mod_args = dict(runtime.mod_args)
        if request.mode == "alignment":
            alignment_language = _resolve_alignment_language(file_data)
            if alignment_language is not None:
                mod_args["language"] = resolve_language_code(alignment_language)

        def run_mod() -> None:
            try:
                _update_status(status_context, "mod", index, f"Processing {request.mode}")
                result_value = runtime.mod_func(audio_path, mod_src, **mod_args)
                runtime.result_queue.put(result_value)
            except Exception as exc:
                if "'NoneType'" in str(exc) and request.mode == "refinement":
                    try:
                        _update_status(status_context, "mod", index, "Re-transcribing...")
                        result_value = runtime.model.transcribe(
                            audio_path,
                            **get_transcribe_args(runtime.model.transcribe, runtime.runtime_settings.snapshot),
                        )
                        result_value = runtime.mod_func(audio_path, result_value, **mod_args)
                        runtime.result_queue.put(result_value)
                    except Exception as retry_exc:
                        fail_status.capture(Exception(f"Re-transcribe failed: {retry_exc}"))
                else:
                    fail_status.capture(exc)

        fail_status = WorkerFailure()
        result = execute_queue_task_fn(
            run_mod,
            cancel_check=processing_state.is_file_processing,
            fail_status=fail_status,
            raise_failure=False,
            result_queue=runtime.result_queue,
        )

        if fail_status.failed:
            _update_status(status_context, "mod", index, "Failed")
            continue

        result = split_res(result, runtime.runtime_settings.snapshot)
        if not result.language:
            result.language = mod_args.get("language", "auto")

        save_output_stable_ts(
            result,
            export_plan.save_base_path,
            runtime.runtime_settings.export_to,
            runtime.runtime_settings,
        )
        processing_state.increment_mod_counter()
        _update_status(status_context, "mod", index, runtime.action)
        _save_export_plan_metadata(
            export_plan,
            {"meta_written_at": str(datetime.now()), "task": f"Mod Result ({request.mode})", "time": time_fn() - runtime.started_at},
        )

    while processing_state.is_file_processing() and is_still_active():
        sleep_fn(0.5)

    logger.info(f"Process MOD completed in {time_fn() - runtime.started_at:.2f}s")
    if processing_state.mod_counter() > 0 and runtime.runtime_settings.should_auto_open_dir(request.mode):
        open_dir_fn(runtime.export_dir)


def process_translate_result_batch(
    request,
    runtime,
    *,
    open_dir_fn: Callable[[str], None] = start_file,
    run_worker_fn: Callable[..., None] = run_monitored_worker,
    sleep_fn: Callable[[float], None] = sleep,
    time_fn: Callable[[], float] = time,
) -> None:
    status_context = runtime.status_context
    processing_state = runtime.processing_state
    processing_state.reset_mod_counter()

    runtime.ui_bridge.init_file_batch(f"Task Translate with {request.engine}", request.data_files)

    def is_still_active():
        return status_context.has_active_work(len(request.data_files))

    for index, file_path in enumerate(request.data_files):
        if not processing_state.is_file_processing():
            break

        try:
            result = runtime.stable_whisper_api.WhisperResult(file_path)
        except Exception:
            _update_status(status_context, "mod", index, "Parse Error")
            continue

        lang_src = to_language_name(result.language) or "auto"
        file_name = _slice_display_name(file_path, start=runtime.slice_start, end=runtime.slice_end)
        base_name = _build_base_export_name(
            datetime.now().strftime(runtime.runtime_settings.export_format),
            file_name,
            lang_src,
            request.lang_target,
            "",
            request.engine,
        )
        format_dict = get_task_format(
            "translated result",
            f"translated result from {lang_src} to {request.lang_target}",
            f"translated result with {request.engine}",
            f"translated result from {lang_src} to {request.lang_target} with {request.engine}",
        )
        format_dict.update(
            get_task_format(
                "tl res",
                f"tl res from {lang_src} to {request.lang_target}",
                f"tl res with {request.engine}",
                f"tl res from {lang_src} to {request.lang_target} with {request.engine}",
                short_only=True,
            )
        )
        export_plan = _build_export_plan(runtime.export_dir, base_name, format_dict)

        _update_status(status_context, "mod", index, "Translating please wait...")
        fail_status = WorkerFailure()

        run_worker_fn(
            run_translate_api,
            cancel_check=processing_state.is_file_processing,
            args=(result, request.engine, lang_src, request.lang_target, fail_status, runtime.settings),
            kwargs=runtime.api_kwargs,
        )

        if fail_status.failed:
            _update_status(status_context, "mod", index, "Failed")
            continue

        processing_state.increment_mod_counter()
        save_output_stable_ts(
            split_res(result, runtime.runtime_settings.snapshot),
            export_plan.save_base_path,
            runtime.runtime_settings.export_to,
            runtime.runtime_settings,
            source_media_path=file_path,
        )
        _update_status(status_context, "mod", index, "Translated")
        _save_export_plan_metadata(
            export_plan,
            {"meta_written_at": str(datetime.now()), "task": "Translate JSON", "time": time_fn() - runtime.started_at},
        )

    while processing_state.is_file_processing() and is_still_active():
        sleep_fn(0.5)

    logger.info(f"Process TL JSON completed in {time_fn() - runtime.started_at:.2f}s")
    if processing_state.mod_counter() > 0 and runtime.runtime_settings.auto_open_dir_translate:
        open_dir_fn(runtime.export_dir)


__all__ = [
    "process_mod_batch",
    "process_translate_result_batch",
]
