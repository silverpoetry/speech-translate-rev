from __future__ import annotations

from time import sleep
from typing import Callable

from speech_translate.utils.audio.record_types import (
    AudioTarget,
    RecordingModelRuntime,
    RecordingSessionConfig,
    RecordingSessionLifecycle,
)


def run_recording_session_loop(
    *,
    lifecycle: RecordingSessionLifecycle,
    config: RecordingSessionConfig,
    model_runtime: RecordingModelRuntime,
    is_tc: bool,
    is_tl: bool,
    control,
    runtime_text_state=None,
    consume_record_loop_input_fn: Callable[..., bytes | None],
    advance_recording_buffer_fn: Callable[..., bool],
    build_record_audio_target_fn: Callable[..., AudioTarget],
    execute_recording_iteration_fn: Callable[..., bool],
    cleanup_processed_audio_target_fn: Callable[..., None],
    break_buffer_and_update_state_fn: Callable[..., None],
    sleep_fn: Callable[[float], None] = sleep,
) -> None:
    while control.is_recording():
        if lifecycle.session_state.paused:
            sleep_fn(0.1)
            continue

        data = consume_record_loop_input_fn(
            lifecycle.session_state,
            lifecycle.callback_ctx,
            config=config,
            is_tc=is_tc,
            sr_divider=lifecycle.sr_divider,
            samp_width=lifecycle.samp_width,
            num_of_channels=lifecycle.num_of_channels,
            translator=lifecycle.services.translator,
            buffer_reducer=lifecycle.services.buffer_reducer,
            control=control,
            runtime_text_state=runtime_text_state,
        )
        if data is None:
            continue

        if not advance_recording_buffer_fn(
            lifecycle.session_state,
            data,
            transcribe_rate=config.transcribe_rate,
            samp_width=lifecycle.samp_width,
            num_of_channels=lifecycle.num_of_channels,
            sr_divider=lifecycle.sr_divider,
            min_input_length=config.min_input_length,
            control=control,
        ):
            continue

        audio_target = build_record_audio_target_fn(
            lifecycle.session_state,
            use_temp=config.use_temp,
            num_of_channels=lifecycle.num_of_channels,
            samp_width=lifecycle.samp_width,
            demucs_enabled=model_runtime.demucs_enabled,
            cuda_device=model_runtime.cuda_device,
            sr_ori=lifecycle.sr_ori,
        )

        if not execute_recording_iteration_fn(
            audio_target=audio_target,
            session_state=lifecycle.session_state,
            is_tc=is_tc,
            is_tl=is_tl,
            config=config,
            model_runtime=model_runtime,
            translator=lifecycle.services.translator,
            control=control,
            runtime_text_state=runtime_text_state,
        ):
            continue

        cleanup_processed_audio_target_fn(
            audio_target,
            use_temp=config.use_temp,
            keep_temp=lifecycle.services.runtime.keep_temp,
            is_tl=is_tl,
            tl_engine_whisper=config.tl_engine_whisper,
            session_state=lifecycle.session_state,
        )

        if lifecycle.session_state.duration_seconds > config.max_buffer_s:
            break_buffer_and_update_state_fn(
                reason="buffer_full",
                session_state=lifecycle.session_state,
                is_tc=is_tc,
                sr_divider=lifecycle.sr_divider,
                samp_width=lifecycle.samp_width,
                num_of_channels=lifecycle.num_of_channels,
                sentence_limitless=config.sentence_limitless,
                max_sentences=config.max_sentences,
                separator=config.separator,
                translator=lifecycle.services.translator,
                buffer_reducer=lifecycle.services.buffer_reducer,
                runtime_text_state=runtime_text_state,
            )
        if control.current_status() == "▶️ Recording ⟳ Transcribing Audio":
            control.set_current_status("▶️ Recording")
