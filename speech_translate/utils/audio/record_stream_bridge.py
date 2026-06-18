from __future__ import annotations

from typing import Callable, Mapping

from speech_translate._constants import WHISPER_SR
from speech_translate._logging import logger
from speech_translate.runtime_registry import settings_registry
from speech_translate.utils.audio import record_streaming as streaming_module
from speech_translate.utils.audio.audio import get_db, get_speech_webrtc, resample_sr, to_silero
from speech_translate.utils.audio.device import get_device_details, get_pyaudio_module
from speech_translate.utils.audio.record_streaming import CallbackContextStore, StreamingStateAdapter
from speech_translate.utils.audio.record_types import (
    RecordingSessionConfig,
    RecordingStreamRuntime,
    RealtimeCallbackContext,
    RealtimeSharedState,
    SileroVadLike,
)


def _stream_settings_snapshot(settings_snapshot: Mapping[str, object] | None = None) -> Mapping[str, object]:
    return dict(settings_registry.get().cache) if settings_snapshot is None else settings_snapshot


def get_callback_context() -> RealtimeCallbackContext | None:
    return streaming_module.get_callback_context()


def reset_callback_context() -> None:
    streaming_module.reset_callback_context()


def initialize_callback_context(
    *,
    sample_rate: int,
    chunk_size: int,
    threshold_enable: bool,
    threshold_db: float,
    threshold_auto: bool,
    use_silero: bool,
    silero_min_conf: float,
    num_of_channels: int,
    samp_width: int,
    use_temp: bool,
    webrtc_vad: object,
    silero_vad: SileroVadLike,
    shared_runtime_state: RealtimeSharedState | None = None,
    store: CallbackContextStore | None = None,
) -> RealtimeCallbackContext:
    return streaming_module.initialize_callback_context(
        sample_rate=sample_rate,
        chunk_size=chunk_size,
        threshold_enable=threshold_enable,
        threshold_db=threshold_db,
        threshold_auto=threshold_auto,
        use_silero=use_silero,
        silero_min_conf=silero_min_conf,
        num_of_channels=num_of_channels,
        samp_width=samp_width,
        use_temp=use_temp,
        webrtc_vad=webrtc_vad,
        silero_vad=silero_vad,
        shared_runtime_state=shared_runtime_state,
        store=store,
    )


def load_recording_vad_runtime(*, rec_type: str, settings_snapshot=None) -> tuple[object, SileroVadLike]:
    return streaming_module.load_recording_vad_runtime(rec_type=rec_type, settings_snapshot=settings_snapshot)


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
    pyaudio = get_pyaudio_module()
    return streaming_module.build_recording_stream_runtime(
        rec_type=rec_type,
        config=config,
        p=p,
        get_device_details_fn=get_device_details if get_device_details_fn is None else get_device_details_fn,
        load_recording_vad_runtime_fn=(
            load_recording_vad_runtime if load_recording_vad_runtime_fn is None else load_recording_vad_runtime_fn
        ),
        initialize_callback_context_fn=(
            initialize_callback_context if initialize_callback_context_fn is None else initialize_callback_context_fn
        ),
        audio_format=pyaudio.paInt16 if audio_format is None else audio_format,
        logger_instance=logger if logger_instance is None else logger_instance,
        settings_snapshot=_stream_settings_snapshot(settings_snapshot),
        shared_runtime_state=shared_runtime_state,
        callback_context_store_instance=callback_context_store_instance,
    )


def open_recording_stream(
    *,
    p,
    stream_runtime: RecordingStreamRuntime,
    record_cb_default: Callable,
    record_cb_override: Callable | None = None,
    state_adapter: StreamingStateAdapter | None = None,
) -> None:
    streaming_module.open_recording_stream(
        p=p,
        stream_runtime=stream_runtime,
        record_cb=record_cb_default if record_cb_override is None else record_cb_override,
        state_adapter=state_adapter,
    )


def prime_realtime_vad(
    ctx: RealtimeCallbackContext,
    resampled: bytes,
    *,
    get_speech_webrtc_fn: Callable[..., object] | None = None,
    to_silero_fn: Callable[..., object] | None = None,
) -> None:
    streaming_module.prime_realtime_vad(
        ctx,
        resampled,
        get_speech_webrtc_fn=get_speech_webrtc if get_speech_webrtc_fn is None else get_speech_webrtc_fn,
        to_silero_fn=to_silero if to_silero_fn is None else to_silero_fn,
    )


def detect_realtime_speech(
    ctx: RealtimeCallbackContext,
    in_data: bytes,
    resampled: bytes,
    *,
    prime_realtime_vad_fn: Callable[..., None] | None = None,
    get_db_fn: Callable[[bytes], float] | None = None,
    get_speech_webrtc_fn: Callable[..., object] | None = None,
    to_silero_fn: Callable[..., object] | None = None,
) -> tuple[bool, bytes]:
    return streaming_module.detect_realtime_speech(
        ctx,
        in_data,
        resampled,
        prime_realtime_vad_fn=prime_realtime_vad if prime_realtime_vad_fn is None else prime_realtime_vad_fn,
        get_db_fn=get_db if get_db_fn is None else get_db_fn,
        get_speech_webrtc_fn=get_speech_webrtc if get_speech_webrtc_fn is None else get_speech_webrtc_fn,
        to_silero_fn=to_silero if to_silero_fn is None else to_silero_fn,
    )


def update_realtime_queue_state(
    ctx: RealtimeCallbackContext,
    *,
    is_speech: bool,
    data_to_queue: bytes,
    state_adapter: StreamingStateAdapter | None = None,
) -> None:
    streaming_module.update_realtime_queue_state(
        ctx,
        is_speech=is_speech,
        data_to_queue=data_to_queue,
        state_adapter=state_adapter,
    )


def handle_record_callback_error(ctx: RealtimeCallbackContext | None, exc: Exception) -> None:
    streaming_module.handle_record_callback_error(ctx, exc)


def execute_record_callback(
    in_data,
    _frame_count,
    _time_info,
    _status,
    *,
    callback_ctx: RealtimeCallbackContext | None,
    state_adapter: StreamingStateAdapter | None = None,
    pyaudio_module=None,
    resample_sr_fn: Callable[[bytes, int, int], bytes] | None = None,
    detect_realtime_speech_fn: Callable[..., tuple[bool, bytes]] | None = None,
    update_realtime_queue_state_fn: Callable[..., None] | None = None,
    handle_record_callback_error_fn: Callable[[RealtimeCallbackContext | None, Exception], None] | None = None,
):
    pyaudio = get_pyaudio_module() if pyaudio_module is None else pyaudio_module
    try:
        if callback_ctx is None:
            return (in_data, pyaudio.paContinue)

        resampled = (resample_sr if resample_sr_fn is None else resample_sr_fn)(in_data, callback_ctx.sample_rate, WHISPER_SR)
        is_speech, data_to_queue = (
            detect_realtime_speech if detect_realtime_speech_fn is None else detect_realtime_speech_fn
        )(callback_ctx, in_data, resampled)
        (update_realtime_queue_state if update_realtime_queue_state_fn is None else update_realtime_queue_state_fn)(
            callback_ctx,
            is_speech=is_speech,
            data_to_queue=data_to_queue,
            state_adapter=state_adapter,
        )

        return (in_data, pyaudio.paContinue)
    except Exception as exc:
        (handle_record_callback_error if handle_record_callback_error_fn is None else handle_record_callback_error_fn)(
            callback_ctx,
            exc,
        )
        return (in_data, pyaudio.paContinue)


def build_record_callback(
    callback_ctx: RealtimeCallbackContext | None,
    *,
    state_adapter: StreamingStateAdapter | None = None,
    execute_record_callback_fn: Callable[..., tuple[bytes, object]] | None = None,
    pyaudio_module=None,
    resample_sr_fn: Callable[[bytes, int, int], bytes] | None = None,
    detect_realtime_speech_fn: Callable[..., tuple[bool, bytes]] | None = None,
    update_realtime_queue_state_fn: Callable[..., None] | None = None,
    handle_record_callback_error_fn: Callable[[RealtimeCallbackContext | None, Exception], None] | None = None,
):
    def _session_record_cb(in_data, frame_count, time_info, status):
        return (execute_record_callback if execute_record_callback_fn is None else execute_record_callback_fn)(
            in_data,
            frame_count,
            time_info,
            status,
            callback_ctx=callback_ctx,
            state_adapter=state_adapter,
            pyaudio_module=pyaudio_module,
            resample_sr_fn=resample_sr_fn,
            detect_realtime_speech_fn=detect_realtime_speech_fn,
            update_realtime_queue_state_fn=update_realtime_queue_state_fn,
            handle_record_callback_error_fn=handle_record_callback_error_fn,
        )

    return _session_record_cb
