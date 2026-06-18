from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from queue import Empty
from time import time
from typing import Callable

from speech_translate.utils.audio.record_runtime import BufferStateReducer, RecordingTextState, TranslationDispatcher
from speech_translate.utils.audio.record_types import AudioTarget, RecordingModelRuntime, RecordingSessionConfig, RealtimeCallbackContext, RealtimeSessionState


def cleanup_processed_audio_target(
    audio_target: AudioTarget,
    *,
    use_temp: bool,
    keep_temp: bool,
    is_tl: bool,
    tl_engine_whisper: bool,
    session_state: RealtimeSessionState,
    remove_file_fn: Callable[[str], None] = os.remove,
) -> None:
    if not use_temp or keep_temp or not isinstance(audio_target, str):
        return
    if is_tl and tl_engine_whisper:
        return
    try:
        remove_file_fn(audio_target)
        session_state.temp_audio_paths.remove(audio_target)
    except Exception:
        pass


def execute_recording_iteration(
    *,
    audio_target: AudioTarget,
    session_state: RealtimeSessionState,
    is_tc: bool,
    is_tl: bool,
    config: RecordingSessionConfig,
    model_runtime: RecordingModelRuntime,
    translator: TranslationDispatcher,
    control,
    runtime_text_state: RecordingTextState | None = None,
    execute_realtime_transcription_fn: Callable[..., object | None],
    filter_realtime_transcription_result_fn: Callable[..., object | None],
    commit_realtime_transcription_fn: Callable[..., None],
) -> bool:
    if is_tl and config.tl_engine_whisper and not is_tc:
        control.set_current_status("▶️ Recording ⟳ Translating Audio")
        translator.dispatch(audio_target, "")
        return True

    control.set_current_status("▶️ Recording ⟳ Transcribing Audio")
    session_state.prev_tc_buffer_seconds = session_state.duration_seconds

    if model_runtime.stable_tc is None:
        return False

    result = execute_realtime_transcription_fn(
        audio_target,
        model_runtime.stable_tc,
        model_runtime.whisper_args,
        transcription_lock=session_state.transcription_lock,
    )
    if result is None:
        return False

    result = filter_realtime_transcription_result_fn(
        result,
        hallucination_filters=model_runtime.hallucination_filters,
        auto=config.auto,
        configured_language=model_runtime.configured_whisper_language,
    )
    commit_realtime_transcription_fn(
        result,
        audio_target=audio_target,
        is_tl=is_tl,
        separator=config.separator,
        translator=translator,
        runtime_text_state=runtime_text_state,
        set_current_status=control.set_current_status,
    )
    return True


def drain_pending_audio(
    session_state: RealtimeSessionState,
    control,
) -> None:
    while not control.data_queue_empty():
        session_state.append_audio(control.get_data_nowait())


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
    control,
    runtime_text_state: RecordingTextState | None = None,
    break_buffer_and_update_state_fn: Callable[..., None],
    utc_now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
    now_fn: Callable[[], float] = time,
) -> bytes | None:
    try:
        return control.get_data(timeout=0.1)
    except Empty:
        if config.auto_break_buffer and callback_ctx.is_silence and now_fn() - callback_ctx.silence_started_at > 1:
            callback_ctx.is_silence = False
            break_buffer_and_update_state_fn(
                reason="silence",
                session_state=session_state,
                is_tc=is_tc,
                sr_divider=sr_divider,
                samp_width=samp_width,
                num_of_channels=num_of_channels,
                sentence_limitless=config.sentence_limitless,
                max_sentences=config.max_sentences,
                separator=config.separator,
                translator=translator,
                buffer_reducer=buffer_reducer,
                runtime_text_state=runtime_text_state,
            )
            control.set_current_status("▶️ Recording (Waiting for speech)")
        if not session_state.last_sample or (
            session_state.next_transcribe_time and session_state.next_transcribe_time > utc_now_fn()
        ):
            return None
        return b""


def advance_recording_buffer(
    session_state: RealtimeSessionState,
    data: bytes,
    *,
    transcribe_rate: timedelta,
    samp_width: int,
    num_of_channels: int,
    sr_divider: int,
    min_input_length: float,
    control,
    drain_pending_audio_fn: Callable[[RealtimeSessionState, object], None] = drain_pending_audio,
    utc_now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> bool:
    now = utc_now_fn()
    if not session_state.next_transcribe_time:
        session_state.next_transcribe_time = now + transcribe_rate

    session_state.append_audio(data)
    drain_pending_audio_fn(session_state, control)

    if session_state.next_transcribe_time > now:
        return False
    session_state.next_transcribe_time = now + transcribe_rate

    session_state.recalculate_duration(
        samp_width=samp_width,
        num_of_channels=num_of_channels,
        sr_divider=sr_divider,
    )
    return session_state.duration_seconds >= min_input_length
