from __future__ import annotations

import os
from threading import Thread
from time import gmtime, sleep, strftime, time
from typing import Callable

from speech_translate.log_helpers import logger
from speech_translate.utils.audio.record_runtime import RecordingStatusEmitter, RecordingTextState, TranslationDispatcher, build_recording_text_state
from speech_translate.utils.audio.record_types import AudioTarget, RecordingSessionFinalizeContext, RealtimeSessionState


def cleanup_temp_audio_paths(
    temp_audio_paths: list[str],
    *,
    remove_file_fn: Callable[[str], None] = os.remove,
) -> None:
    for audio in temp_audio_paths:
        try:
            remove_file_fn(audio)
        except Exception:
            pass


def cleanup_translation_audio(
    audio_target: AudioTarget | None,
    *,
    remove_file_fn: Callable[[str], None] = os.remove,
) -> None:
    if isinstance(audio_target, str):
        try:
            remove_file_fn(audio_target)
        except Exception:
            pass


def build_recording_sentence_count_text(
    *,
    sentence_limitless: bool,
    max_sentences: int,
    runtime_text_state: RecordingTextState | None = None,
    build_text_state_fn: Callable[[], RecordingTextState] = build_recording_text_state,
) -> str:
    runtime_text_state = runtime_text_state or build_text_state_fn()
    sentence_count_text = f"{len(runtime_text_state.transcribed_sentences()) or len(runtime_text_state.translated_sentences()) or '0'}"
    if not sentence_limitless:
        sentence_count_text += f"/{max_sentences}"
    return sentence_count_text


def run_recording_status_loop(
    session_state: RealtimeSessionState,
    status_emitter: RecordingStatusEmitter,
    *,
    t_start: float,
    max_buffer_s: int,
    max_sentences: int,
    sentence_limitless: bool,
    control,
    runtime_text_state: RecordingTextState | None = None,
    build_sentence_count_text_fn: Callable[..., str] = build_recording_sentence_count_text,
    build_text_state_fn: Callable[[], RecordingTextState] = build_recording_text_state,
    sleep_fn: Callable[[float], None] = sleep,
    now_fn: Callable[[], float] = time,
    elapsed_formatter: Callable[[float], str] = lambda elapsed: strftime("%H:%M:%S", gmtime(elapsed)),
) -> None:
    runtime_text_state = runtime_text_state or build_text_state_fn()
    while control.is_recording():
        if session_state.paused:
            sleep_fn(0.1)
            continue
        try:
            status_emitter.emit(
                status=control.current_status(),
                timer=elapsed_formatter(now_fn() - t_start),
                buffer_text=f"{round(session_state.duration_seconds, 2)}/{round(max_buffer_s, 2)} sec",
                sentences=build_sentence_count_text_fn(
                    sentence_limitless=sentence_limitless,
                    max_sentences=max_sentences,
                    runtime_text_state=runtime_text_state,
                ),
            )
            sleep_fn(0.1)
        except Exception as exc:
            logger.exception(exc)
            break


def start_translation_dispatcher_thread(
    translator: TranslationDispatcher,
    control,
    *,
    cleanup_translation_audio_fn: Callable[[AudioTarget | None], None] = cleanup_translation_audio,
    thread_factory: Callable[..., Thread] = Thread,
) -> None:
    thread_factory(
        target=lambda: translator.close(control.is_recording, cleanup_translation_audio_fn),
        daemon=True,
    ).start()


def start_recording_status_thread(
    session_state: RealtimeSessionState,
    status_emitter: RecordingStatusEmitter,
    *,
    t_start: float,
    max_buffer_s: int,
    max_sentences: int,
    sentence_limitless: bool,
    control,
    runtime_text_state: RecordingTextState | None = None,
    run_status_loop_fn: Callable[..., None] = run_recording_status_loop,
    thread_factory: Callable[..., Thread] = Thread,
) -> None:
    thread_factory(
        target=lambda: run_status_loop_fn(
            session_state,
            status_emitter,
            t_start=t_start,
            max_buffer_s=max_buffer_s,
            max_sentences=max_sentences,
            sentence_limitless=sentence_limitless,
            control=control,
            runtime_text_state=runtime_text_state,
        ),
        daemon=True,
    ).start()


def finalize_recording_session(
    p,
    finalize_context: RecordingSessionFinalizeContext,
    control,
    *,
    drain_audio_queue_fn: Callable[[object], None],
    cleanup_temp_audio_paths_fn: Callable[[list[str]], None] = cleanup_temp_audio_paths,
    reset_callback_context_fn: Callable[[], None],
) -> None:
    if finalize_context.update_status is not None:
        control.set_current_status("⚠️ Stopping stream")
        finalize_context.update_status()
    if stream := control.stream():
        stream.stop_stream()
        stream.close()
        control.clear_stream()
    control.clear_runtime_threads()

    if finalize_context.update_status is not None:
        control.set_current_status("⚠️ Terminating pyaudio")
        finalize_context.update_status()
    p.terminate()

    drain_audio_queue_fn(control)
    if finalize_context.session_state is not None and not finalize_context.keep_temp:
        cleanup_temp_audio_paths_fn(finalize_context.session_state.temp_audio_paths)

    reset_callback_context_fn()
    control.set_current_status("⏹️ Stopped")
    if finalize_context.update_status is not None:
        finalize_context.update_status()
