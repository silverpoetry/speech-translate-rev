from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from threading import Lock
from time import time
from typing import Callable, Mapping

from speech_translate._logging import logger
from speech_translate.runtime_registry import settings_registry
from speech_translate.utils.audio import record_processing as processing_module
from speech_translate.utils.audio.record_runtime import (
    BufferStateReducer,
    RecordingSettingsAdapter,
    RecordingStatusEmitter,
    RecordingTextState,
    TranslationDispatcher,
    build_recording_text_state,
)
from speech_translate.utils.audio.record_session_bootstrap import (
    RecordingSessionControl,
    build_recording_model_runtime as _build_recording_model_runtime_impl,
    build_recording_session_services as _build_recording_session_services_impl,
    initialize_recording_session_lifecycle as _initialize_recording_session_lifecycle_impl,
    prepare_recording_session_bootstrap as _prepare_recording_session_bootstrap_impl,
    start_recording_session_support_threads as _start_recording_session_support_threads_impl,
)
from speech_translate.utils.audio.record_session_iteration import (
    advance_recording_buffer as _advance_recording_buffer_impl,
    cleanup_processed_audio_target as _cleanup_processed_audio_target_impl,
    consume_record_loop_input as _consume_record_loop_input_impl,
    drain_pending_audio as _drain_pending_audio_impl,
    execute_recording_iteration as _execute_recording_iteration_impl,
)
from speech_translate.utils.audio.record_session_loop import run_recording_session_loop as _run_recording_session_loop_impl
from speech_translate.utils.audio.record_session_support import (
    build_recording_sentence_count_text as _build_recording_sentence_count_text_impl,
    cleanup_temp_audio_paths as _cleanup_temp_audio_paths_impl,
    cleanup_translation_audio as _cleanup_translation_audio_impl,
    finalize_recording_session as _finalize_recording_session_impl,
    run_recording_status_loop as _run_recording_status_loop_impl,
    start_recording_status_thread as _start_recording_status_thread_impl,
    start_translation_dispatcher_thread as _start_translation_dispatcher_thread_impl,
)
from speech_translate.utils.audio.record_settings import (
    build_recording_session_config as build_recording_session_settings_config,
)
from speech_translate.utils.audio.record_stream_bridge import (
    build_record_callback,
    build_recording_stream_runtime as _build_recording_stream_runtime_impl,
    get_callback_context as _get_callback_context_impl,
    open_recording_stream as _open_recording_stream_impl,
    reset_callback_context as _reset_callback_context_impl,
)
from speech_translate.utils.audio.record_streaming import CallbackContextStore, StreamingStateAdapter
from speech_translate.utils.audio.record_types import (
    AudioTarget,
    RecordingModelRuntime,
    RecordingRuntime,
    RecordingSessionConfig,
    RecordingSessionFinalizeContext,
    RecordingSessionLifecycle,
    RecordingSessionServices,
    RecordingStreamRuntime,
    RealtimeCallbackContext,
    RealtimeSessionState,
    RealtimeSharedState,
    SileroVadLike,
    TranscriptionResultLike,
    WhisperCallable,
)
from speech_translate.utils.translate.language import get_whisper_lang_name
from speech_translate.utils.whisper.helper import get_hallucination_filter
from speech_translate.utils.whisper.result import remove_segments_by_str


def _get_whisper_runtime_api():
    from speech_translate.utils.whisper import load as whisper_load_api

    return whisper_load_api


def get_model(*args, **kwargs):
    return _get_whisper_runtime_api().get_model(*args, **kwargs)


def get_model_args(*args, **kwargs):
    return _get_whisper_runtime_api().get_model_args(*args, **kwargs)


def get_tc_args(*args, **kwargs):
    return _get_whisper_runtime_api().get_tc_args(*args, **kwargs)


def get_recording_settings_store() -> RecordingSettingsAdapter:
    return RecordingSettingsAdapter(cache=dict(settings_registry.get().cache))


def recording_settings_snapshot(settings_snapshot: Mapping[str, object] | None = None) -> Mapping[str, object]:
    return get_recording_settings_store().cache if settings_snapshot is None else settings_snapshot


def build_recording_session_config(
    *,
    rec_type: str,
    lang_source: str,
    engine: str,
    is_tc: bool,
    is_tl: bool,
    settings_snapshot: Mapping[str, object] | None = None,
) -> RecordingSessionConfig:
    return build_recording_session_settings_config(
        rec_type=rec_type,
        lang_source=lang_source,
        engine=engine,
        is_tc=is_tc,
        is_tl=is_tl,
        settings_snapshot=recording_settings_snapshot(settings_snapshot),
    )


def load_recording_model_runtime(
    *,
    config: RecordingSessionConfig,
    lang_source: str,
    model_name_tc: str,
    engine: str,
    is_tc: bool,
    is_tl: bool,
    settings_snapshot: Mapping[str, object] | None = None,
) -> RecordingModelRuntime:
    from speech_translate.utils.whisper.prompts import pick_initial_prompt

    return _build_recording_model_runtime_impl(
        config=config,
        lang_source=lang_source,
        model_name_tc=model_name_tc,
        engine=engine,
        is_tc=is_tc,
        is_tl=is_tl,
        settings_snapshot=recording_settings_snapshot(settings_snapshot),
        get_model_fn=get_model,
        get_model_args_fn=get_model_args,
        get_tc_args_fn=get_tc_args,
        get_hallucination_filter_fn=get_hallucination_filter,
        initial_prompt_picker=pick_initial_prompt,
    )


def build_recording_stream_runtime(
    *,
    rec_type: str,
    config: RecordingSessionConfig,
    p,
    settings_snapshot: Mapping[str, object] | None = None,
    shared_runtime_state: RealtimeSharedState | None = None,
    callback_context_store_instance: CallbackContextStore | None = None,
    get_device_details_fn: Callable[..., tuple[bool, dict[str, object]]] | None = None,
    load_recording_vad_runtime_fn: Callable[..., tuple[object, SileroVadLike]] | None = None,
    initialize_callback_context_fn: Callable[..., RealtimeCallbackContext] | None = None,
    audio_format: object | None = None,
    logger_instance=None,
) -> RecordingStreamRuntime:
    return _build_recording_stream_runtime_impl(
        rec_type=rec_type,
        config=config,
        p=p,
        settings_snapshot=recording_settings_snapshot(settings_snapshot),
        shared_runtime_state=shared_runtime_state,
        callback_context_store_instance=callback_context_store_instance,
        get_device_details_fn=get_device_details_fn,
        load_recording_vad_runtime_fn=load_recording_vad_runtime_fn,
        initialize_callback_context_fn=initialize_callback_context_fn,
        audio_format=audio_format,
        logger_instance=logger if logger_instance is None else logger_instance,
    )


def prepare_recording_session_bootstrap(
    *,
    rec_type: str,
    settings_snapshot: Mapping[str, object],
    lang_source: str,
    engine: str,
    model_name_tc: str,
    is_tc: bool,
    is_tl: bool,
    p,
    shared_runtime_state: RealtimeSharedState | None = None,
    callback_context_store_instance: CallbackContextStore | None = None,
    build_config_fn: Callable[..., RecordingSessionConfig] | None = None,
    load_model_runtime_fn: Callable[..., RecordingModelRuntime] | None = None,
    build_stream_runtime_fn: Callable[..., RecordingStreamRuntime] | None = None,
):
    return _prepare_recording_session_bootstrap_impl(
        rec_type=rec_type,
        settings_snapshot=settings_snapshot,
        lang_source=lang_source,
        engine=engine,
        model_name_tc=model_name_tc,
        is_tc=is_tc,
        is_tl=is_tl,
        p=p,
        shared_runtime_state=shared_runtime_state or RealtimeSharedState(),
        callback_context_store_instance=callback_context_store_instance,
        build_config_fn=build_recording_session_config if build_config_fn is None else build_config_fn,
        load_model_runtime_fn=load_recording_model_runtime if load_model_runtime_fn is None else load_model_runtime_fn,
        build_stream_runtime_fn=build_recording_stream_runtime if build_stream_runtime_fn is None else build_stream_runtime_fn,
    )


def build_recording_sentence_count_text(
    *,
    sentence_limitless: bool,
    max_sentences: int,
    runtime_text_state: RecordingTextState | None = None,
) -> str:
    return _build_recording_sentence_count_text_impl(
        sentence_limitless=sentence_limitless,
        max_sentences=max_sentences,
        runtime_text_state=runtime_text_state,
        build_text_state_fn=build_recording_text_state,
    )


def run_recording_status_loop(
    session_state: RealtimeSessionState,
    status_emitter: RecordingStatusEmitter,
    *,
    t_start: float,
    max_buffer_s: int,
    max_sentences: int,
    sentence_limitless: bool,
    control: RecordingSessionControl,
    runtime_text_state: RecordingTextState | None = None,
) -> None:
    _run_recording_status_loop_impl(
        session_state,
        status_emitter,
        t_start=t_start,
        max_buffer_s=max_buffer_s,
        max_sentences=max_sentences,
        sentence_limitless=sentence_limitless,
        control=control,
        runtime_text_state=runtime_text_state,
        build_sentence_count_text_fn=build_recording_sentence_count_text,
        build_text_state_fn=build_recording_text_state,
    )


def start_translation_dispatcher_thread(
    translator: TranslationDispatcher,
    control: RecordingSessionControl,
) -> None:
    _start_translation_dispatcher_thread_impl(
        translator,
        control,
        cleanup_translation_audio_fn=cleanup_translation_audio,
    )


def start_recording_status_thread(
    session_state: RealtimeSessionState,
    status_emitter: RecordingStatusEmitter,
    *,
    t_start: float,
    max_buffer_s: int,
    max_sentences: int,
    sentence_limitless: bool,
    control: RecordingSessionControl,
    runtime_text_state: RecordingTextState | None = None,
) -> None:
    _start_recording_status_thread_impl(
        session_state,
        status_emitter,
        t_start=t_start,
        max_buffer_s=max_buffer_s,
        max_sentences=max_sentences,
        sentence_limitless=sentence_limitless,
        control=control,
        runtime_text_state=runtime_text_state,
        run_status_loop_fn=run_recording_status_loop,
    )


def build_recording_session_services(
    *,
    config: RecordingSessionConfig,
    model_runtime: RecordingModelRuntime,
    device: str,
    lang_source: str,
    lang_target: str,
    engine: str,
    is_tc: bool,
    is_tl: bool,
    t_start: float,
    control: RecordingSessionControl,
    runtime_text_state: RecordingTextState | None = None,
    status_emitter_factory: Callable[[RecordingRuntime], RecordingStatusEmitter] | None = None,
    translator_factory: Callable[..., TranslationDispatcher] | None = None,
    buffer_reducer_factory: Callable[..., BufferStateReducer] | None = None,
    build_text_state_fn: Callable[[], RecordingTextState] | None = None,
) -> RecordingSessionServices:
    return _build_recording_session_services_impl(
        config=config,
        model_runtime=model_runtime,
        device=device,
        lang_source=lang_source,
        lang_target=lang_target,
        engine=engine,
        is_tc=is_tc,
        is_tl=is_tl,
        t_start=t_start,
        control=control,
        runtime_text_state=runtime_text_state,
        status_emitter_factory=RecordingStatusEmitter if status_emitter_factory is None else status_emitter_factory,
        translator_factory=TranslationDispatcher if translator_factory is None else translator_factory,
        buffer_reducer_factory=BufferStateReducer if buffer_reducer_factory is None else buffer_reducer_factory,
        build_text_state_fn=build_recording_text_state if build_text_state_fn is None else build_text_state_fn,
    )


def initialize_recording_session_lifecycle(
    *,
    config: RecordingSessionConfig,
    model_runtime: RecordingModelRuntime,
    stream_runtime: RecordingStreamRuntime,
    device: str,
    lang_source: str,
    lang_target: str,
    engine: str,
    is_tc: bool,
    is_tl: bool,
    t_start: float,
    control: RecordingSessionControl,
    runtime_text_state: RecordingTextState | None = None,
    build_services_fn: Callable[..., RecordingSessionServices] | None = None,
    build_text_state_fn: Callable[[], RecordingTextState] | None = None,
    lock_factory: Callable[[], object] | None = None,
) -> RecordingSessionLifecycle:
    return _initialize_recording_session_lifecycle_impl(
        config=config,
        model_runtime=model_runtime,
        stream_runtime=stream_runtime,
        device=device,
        lang_source=lang_source,
        lang_target=lang_target,
        engine=engine,
        is_tc=is_tc,
        is_tl=is_tl,
        t_start=t_start,
        control=control,
        runtime_text_state=runtime_text_state,
        build_services_fn=build_recording_session_services if build_services_fn is None else build_services_fn,
        build_text_state_fn=build_recording_text_state if build_text_state_fn is None else build_text_state_fn,
        lock_factory=Lock if lock_factory is None else lock_factory,
    )


def start_recording_session_support_threads(
    *,
    services: RecordingSessionServices,
    session_state: RealtimeSessionState,
    t_start: float,
    max_buffer_s: int,
    max_sentences: int,
    sentence_limitless: bool,
    control: RecordingSessionControl,
    runtime_text_state: RecordingTextState | None = None,
    start_translation_dispatcher_thread_fn: Callable[..., None] | None = None,
    start_recording_status_thread_fn: Callable[..., None] | None = None,
) -> None:
    _start_recording_session_support_threads_impl(
        services=services,
        session_state=session_state,
        t_start=t_start,
        max_buffer_s=max_buffer_s,
        max_sentences=max_sentences,
        sentence_limitless=sentence_limitless,
        control=control,
        runtime_text_state=runtime_text_state,
        start_translation_dispatcher_thread_fn=(
            start_translation_dispatcher_thread
            if start_translation_dispatcher_thread_fn is None
            else start_translation_dispatcher_thread_fn
        ),
        start_recording_status_thread_fn=(
            start_recording_status_thread
            if start_recording_status_thread_fn is None
            else start_recording_status_thread_fn
        ),
    )


def drain_audio_queue(control: RecordingSessionControl) -> None:
    control.clear_data_queue()


def cleanup_temp_audio_paths(
    temp_audio_paths: list[str],
    *,
    remove_file_fn: Callable[[str], None] | None = None,
) -> None:
    _cleanup_temp_audio_paths_impl(
        temp_audio_paths,
        remove_file_fn=os.remove if remove_file_fn is None else remove_file_fn,
    )


def cleanup_translation_audio(
    audio_target: AudioTarget | None,
    *,
    remove_file_fn: Callable[[str], None] | None = None,
) -> None:
    _cleanup_translation_audio_impl(
        audio_target,
        remove_file_fn=os.remove if remove_file_fn is None else remove_file_fn,
    )


def finalize_recording_session(
    p,
    finalize_context: RecordingSessionFinalizeContext,
    control: RecordingSessionControl,
    drain_audio_queue_fn: Callable[[RecordingSessionControl], None] | None = None,
    cleanup_temp_audio_paths_fn: Callable[[list[str]], None] | None = None,
    reset_callback_context_fn: Callable[[], None] | None = None,
) -> None:
    _finalize_recording_session_impl(
        p,
        finalize_context,
        control,
        drain_audio_queue_fn=drain_audio_queue if drain_audio_queue_fn is None else drain_audio_queue_fn,
        cleanup_temp_audio_paths_fn=(
            cleanup_temp_audio_paths if cleanup_temp_audio_paths_fn is None else cleanup_temp_audio_paths_fn
        ),
        reset_callback_context_fn=(
            _reset_callback_context_impl if reset_callback_context_fn is None else reset_callback_context_fn
        ),
    )


def save_to_temp(audio_bytes: bytes, channels: int, samp_width: int, sr: int) -> str:
    return processing_module.save_to_temp(audio_bytes, channels, samp_width, sr)


def bytes_to_numpy(audio_bytes: bytes, channels: int, use_demucs: bool, device: str) -> object:
    return processing_module.bytes_to_numpy(audio_bytes, channels, use_demucs, device)


def build_record_audio_target(
    session_state: RealtimeSessionState,
    *,
    use_temp: bool,
    num_of_channels: int,
    samp_width: int,
    demucs_enabled: bool,
    cuda_device: str,
    sr_ori: int,
    save_to_temp_fn: Callable[[bytes, int, int, int], str] | None = None,
    bytes_to_numpy_fn: Callable[[bytes, int, bool, str], object] | None = None,
) -> AudioTarget:
    return processing_module.build_record_audio_target(
        session_state,
        use_temp=use_temp,
        num_of_channels=num_of_channels,
        samp_width=samp_width,
        demucs_enabled=demucs_enabled,
        cuda_device=cuda_device,
        sr_ori=sr_ori,
        save_to_temp_fn=save_to_temp if save_to_temp_fn is None else save_to_temp_fn,
        bytes_to_numpy_fn=bytes_to_numpy if bytes_to_numpy_fn is None else bytes_to_numpy_fn,
    )


def execute_realtime_transcription(
    audio_target: AudioTarget,
    stable_tc: WhisperCallable,
    whisper_args: dict[str, object],
    *,
    transcription_lock=None,
) -> TranscriptionResultLike | None:
    return processing_module.execute_realtime_transcription(
        audio_target,
        stable_tc,
        whisper_args,
        tc_lock=transcription_lock,
    )


def filter_realtime_transcription_result(
    result: TranscriptionResultLike | None,
    *,
    hallucination_filters: dict[str, object],
    auto: bool,
    configured_language: str | None,
    settings: RecordingSettingsAdapter | None = None,
) -> TranscriptionResultLike | None:
    return processing_module.filter_realtime_transcription_result(
        result,
        hallucination_filters=hallucination_filters,
        auto=auto,
        configured_language=configured_language,
        get_whisper_lang_name=get_whisper_lang_name,
        settings=settings,
        remove_segments_by_str_fn=remove_segments_by_str,
    )


def commit_realtime_transcription(
    result: TranscriptionResultLike | None,
    *,
    audio_target: AudioTarget,
    is_tl: bool,
    separator: str,
    translator: TranslationDispatcher,
    runtime_text_state: RecordingTextState | None = None,
    set_current_status,
) -> None:
    processing_module.commit_realtime_transcription(
        result,
        audio_target=audio_target,
        is_tl=is_tl,
        separator=separator,
        translator=translator,
        runtime_text_state=runtime_text_state or build_recording_text_state(),
        set_current_status=set_current_status,
    )


def utc_now() -> datetime:
    return datetime.now(UTC)


def apply_smart_split(
    *,
    session_state: RealtimeSessionState,
    previous_result: TranscriptionResultLike,
    sr_divider: int,
    samp_width: int,
    num_of_channels: int,
    sentence_limitless: bool,
    max_sentences: int,
    separator: str,
    translator: TranslationDispatcher,
    runtime_text_state: RecordingTextState | None = None,
) -> bool:
    return processing_module.apply_smart_split(
        session_state=session_state,
        previous_result=previous_result,
        sr_divider=sr_divider,
        samp_width=samp_width,
        num_of_channels=num_of_channels,
        sentence_limitless=sentence_limitless,
        max_sentences=max_sentences,
        separator=separator,
        translator=translator,
        utc_now=utc_now,
        runtime_text_state=runtime_text_state or build_recording_text_state(),
    )


def break_buffer_and_update_state(
    *,
    reason: str,
    session_state: RealtimeSessionState,
    is_tc: bool,
    sr_divider: int,
    samp_width: int,
    num_of_channels: int,
    sentence_limitless: bool,
    max_sentences: int,
    separator: str,
    translator: TranslationDispatcher,
    buffer_reducer: BufferStateReducer,
    runtime_text_state: RecordingTextState | None = None,
) -> None:
    processing_module.break_buffer_and_update_state(
        reason=reason,
        session_state=session_state,
        is_tc=is_tc,
        sr_divider=sr_divider,
        samp_width=samp_width,
        num_of_channels=num_of_channels,
        sentence_limitless=sentence_limitless,
        max_sentences=max_sentences,
        separator=separator,
        translator=translator,
        buffer_reducer=buffer_reducer,
        utc_now=utc_now,
        runtime_text_state=runtime_text_state or build_recording_text_state(),
    )


def cleanup_processed_audio_target(
    audio_target: AudioTarget,
    *,
    use_temp: bool,
    keep_temp: bool,
    is_tl: bool,
    tl_engine_whisper: bool,
    session_state: RealtimeSessionState,
) -> None:
    _cleanup_processed_audio_target_impl(
        audio_target,
        use_temp=use_temp,
        keep_temp=keep_temp,
        is_tl=is_tl,
        tl_engine_whisper=tl_engine_whisper,
        session_state=session_state,
        remove_file_fn=os.remove,
    )


def execute_recording_iteration(
    *,
    audio_target: AudioTarget,
    session_state: RealtimeSessionState,
    is_tc: bool,
    is_tl: bool,
    config: RecordingSessionConfig,
    model_runtime: RecordingModelRuntime,
    translator: TranslationDispatcher,
    control: RecordingSessionControl,
    runtime_text_state: RecordingTextState | None = None,
) -> bool:
    return _execute_recording_iteration_impl(
        audio_target=audio_target,
        session_state=session_state,
        is_tc=is_tc,
        is_tl=is_tl,
        config=config,
        model_runtime=model_runtime,
        translator=translator,
        control=control,
        runtime_text_state=runtime_text_state,
        execute_realtime_transcription_fn=execute_realtime_transcription,
        filter_realtime_transcription_result_fn=filter_realtime_transcription_result,
        commit_realtime_transcription_fn=commit_realtime_transcription,
    )


def drain_pending_audio(
    session_state: RealtimeSessionState,
    control: RecordingSessionControl,
) -> None:
    _drain_pending_audio_impl(session_state, control)


def consume_record_loop_input(
    session_state: RealtimeSessionState,
    callback_ctx: RealtimeCallbackContext,
    *,
    config: RecordingSessionConfig,
    is_tc: bool,
    sr_divider: int,
    samp_width: int,
    num_of_channels: int,
    translator: TranslationDispatcher,
    buffer_reducer: BufferStateReducer,
    control: RecordingSessionControl,
    runtime_text_state: RecordingTextState | None = None,
) -> bytes | None:
    return _consume_record_loop_input_impl(
        session_state,
        callback_ctx,
        config=config,
        is_tc=is_tc,
        sr_divider=sr_divider,
        samp_width=samp_width,
        num_of_channels=num_of_channels,
        translator=translator,
        buffer_reducer=buffer_reducer,
        control=control,
        runtime_text_state=runtime_text_state,
        break_buffer_and_update_state_fn=break_buffer_and_update_state,
        utc_now_fn=utc_now,
        now_fn=time,
    )


def advance_recording_buffer(
    session_state: RealtimeSessionState,
    data: bytes,
    *,
    transcribe_rate: timedelta,
    samp_width: int,
    num_of_channels: int,
    sr_divider: int,
    min_input_length: float,
    control: RecordingSessionControl,
) -> bool:
    return _advance_recording_buffer_impl(
        session_state,
        data,
        transcribe_rate=transcribe_rate,
        samp_width=samp_width,
        num_of_channels=num_of_channels,
        sr_divider=sr_divider,
        min_input_length=min_input_length,
        control=control,
        drain_pending_audio_fn=drain_pending_audio,
        utc_now_fn=utc_now,
    )


def run_recording_session_loop(
    *,
    lifecycle: RecordingSessionLifecycle,
    config: RecordingSessionConfig,
    model_runtime: RecordingModelRuntime,
    is_tc: bool,
    is_tl: bool,
    rec_type: str,
    control: RecordingSessionControl,
    runtime_text_state: RecordingTextState | None = None,
    consume_record_loop_input_fn: Callable[..., bytes | None] | None = None,
    advance_recording_buffer_fn: Callable[..., bool] | None = None,
    build_record_audio_target_fn: Callable[..., AudioTarget] | None = None,
    execute_recording_iteration_fn: Callable[..., bool] | None = None,
    cleanup_processed_audio_target_fn: Callable[..., None] | None = None,
    break_buffer_and_update_state_fn: Callable[..., None] | None = None,
) -> None:
    _run_recording_session_loop_impl(
        lifecycle=lifecycle,
        config=config,
        model_runtime=model_runtime,
        is_tc=is_tc,
        is_tl=is_tl,
        control=control,
        runtime_text_state=runtime_text_state,
        consume_record_loop_input_fn=consume_record_loop_input if consume_record_loop_input_fn is None else consume_record_loop_input_fn,
        advance_recording_buffer_fn=advance_recording_buffer if advance_recording_buffer_fn is None else advance_recording_buffer_fn,
        build_record_audio_target_fn=build_record_audio_target if build_record_audio_target_fn is None else build_record_audio_target_fn,
        execute_recording_iteration_fn=execute_recording_iteration if execute_recording_iteration_fn is None else execute_recording_iteration_fn,
        cleanup_processed_audio_target_fn=(
            cleanup_processed_audio_target if cleanup_processed_audio_target_fn is None else cleanup_processed_audio_target_fn
        ),
        break_buffer_and_update_state_fn=(
            break_buffer_and_update_state if break_buffer_and_update_state_fn is None else break_buffer_and_update_state_fn
        ),
    )


def calculate_buffer_duration(
    audio_bytes: bytes,
    *,
    samp_width: int,
    num_of_channels: int,
    sr_divider: int,
) -> float:
    if samp_width <= 0 or num_of_channels <= 0 or sr_divider <= 0:
        return 0.0
    return len(audio_bytes) / (samp_width * num_of_channels * sr_divider)


def _default_record_callback(in_data, frame_count, time_info, status):
    return build_record_callback(_get_callback_context_impl())(in_data, frame_count, time_info, status)


def record_cb(in_data, frame_count, time_info, status):
    return _default_record_callback(in_data, frame_count, time_info, status)


def open_recording_stream(
    *,
    p,
    stream_runtime: RecordingStreamRuntime,
    record_cb_override: Callable | None = None,
    state_adapter: StreamingStateAdapter | None = None,
) -> None:
    _open_recording_stream_impl(
        p=p,
        stream_runtime=stream_runtime,
        record_cb_default=_default_record_callback,
        record_cb_override=record_cb_override,
        state_adapter=state_adapter,
    )


__all__ = [
    "build_record_callback",
    "calculate_buffer_duration",
    "build_recording_session_config",
    "build_recording_session_services",
    "build_recording_sentence_count_text",
    "build_recording_stream_runtime",
    "commit_realtime_transcription",
    "consume_record_loop_input",
    "drain_audio_queue",
    "execute_recording_iteration",
    "execute_realtime_transcription",
    "finalize_recording_session",
    "initialize_recording_session_lifecycle",
    "load_recording_model_runtime",
    "open_recording_stream",
    "prepare_recording_session_bootstrap",
    "record_cb",
    "recording_settings_snapshot",
    "run_recording_session_loop",
    "run_recording_status_loop",
    "start_recording_session_support_threads",
]
