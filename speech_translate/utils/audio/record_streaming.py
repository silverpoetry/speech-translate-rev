from __future__ import annotations

from time import time
from typing import cast

import torch
import torchaudio
import webrtcvad

from speech_translate._constants import WHISPER_SR
from speech_translate._logging import logger
from speech_translate._path import dir_silero_vad
from speech_translate.linker import bc, sj
from speech_translate.utils.audio.audio import get_db, get_frame_duration, get_speech_webrtc, to_silero
from speech_translate.utils.audio.device import get_device_details
from speech_translate.utils.audio.record_runtime import shared_state
from speech_translate.utils.audio.record_types import (
    RecordingSessionConfig,
    RecordingStreamRuntime,
    RealtimeCallbackContext,
    SileroVadLike,
)

if __name__ == "speech_translate.utils.audio.record_streaming":
    from platform import system

    if system() == "Windows":
        import pyaudiowpatch as pyaudio
    else:
        import pyaudio


callback_context: RealtimeCallbackContext | None = None


def get_callback_context() -> RealtimeCallbackContext | None:
    return callback_context


def reset_callback_context() -> None:
    global callback_context
    callback_context = None


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
    webrtc_vad: webrtcvad.Vad,
    silero_vad: SileroVadLike,
) -> RealtimeCallbackContext:
    global callback_context

    callback_context = RealtimeCallbackContext(
        sample_rate=sample_rate,
        frame_duration_ms=get_frame_duration(sample_rate, chunk_size),
        threshold_enable=threshold_enable,
        threshold_db=threshold_db,
        threshold_auto=threshold_auto,
        use_silero=use_silero,
        silero_min_conf=silero_min_conf,
        vad_checked=False,
        num_of_channels=num_of_channels,
        samp_width=samp_width,
        use_temp=use_temp,
        silence_started_at=time(),
        webrtc_vad=webrtc_vad,
        silero_vad=silero_vad,
    )
    return callback_context


def load_recording_vad_runtime(*, rec_type: str) -> tuple[webrtcvad.Vad, SileroVadLike]:
    webrtc_vad = webrtcvad.Vad(sj.cache.get(f"threshold_auto_mode_{rec_type}", 3))

    if callable(getattr(torchaudio, "set_audio_backend", None)):
        try:
            torchaudio.set_audio_backend("soundfile")  # type: ignore
        except Exception:
            pass

    silero_model = torch.hub.load(repo_or_dir=dir_silero_vad, source="local", model="silero_vad", onnx=True)
    silero_vad = cast(SileroVadLike, silero_model[0] if isinstance(silero_model, tuple) else silero_model)
    silero_vad.reset_states()
    return webrtc_vad, silero_vad


def build_recording_stream_runtime(
    *,
    rec_type: str,
    config: RecordingSessionConfig,
    p,
    get_device_details_fn=get_device_details,
    load_recording_vad_runtime_fn=load_recording_vad_runtime,
    initialize_callback_context_fn=initialize_callback_context,
    audio_format=None,
    logger_instance=logger,
) -> RecordingStreamRuntime:
    success, detail = get_device_details_fn(rec_type, sj, p)
    if not success:
        raise Exception("Failed to get device details")

    device_detail = cast(dict[str, object], detail["device_detail"])
    sr_ori = int(detail["sample_rate"])
    num_of_channels = int(detail["num_of_channels"])
    chunk_size = int(detail["chunk_size"])

    if not sj.cache["supress_record_warning"] and sr_ori > 48000:
        logger_instance.warning(f"Sample rate is high ({sr_ori} Hz). May cause issues. Can be suppressed in settings.")

    webrtc_vad, silero_vad = load_recording_vad_runtime_fn(rec_type=rec_type)
    sample_format = pyaudio.paInt16 if audio_format is None else audio_format
    samp_width = p.get_sample_size(sample_format)
    sr_divider = WHISPER_SR if not config.use_temp else sr_ori
    callback_ctx = initialize_callback_context_fn(
        sample_rate=sr_ori,
        chunk_size=chunk_size,
        threshold_enable=config.threshold_enable,
        threshold_db=config.threshold_db,
        threshold_auto=config.threshold_auto,
        use_silero=config.use_silero,
        silero_min_conf=config.silero_min_conf,
        num_of_channels=num_of_channels,
        samp_width=samp_width,
        use_temp=config.use_temp,
        webrtc_vad=webrtc_vad,
        silero_vad=silero_vad,
    )
    return RecordingStreamRuntime(
        input_device_index=int(device_detail["index"]),
        sr_ori=sr_ori,
        num_of_channels=num_of_channels,
        chunk_size=chunk_size,
        samp_width=samp_width,
        sr_divider=sr_divider,
        callback_ctx=callback_ctx,
    )


def open_recording_stream(*, p, stream_runtime: RecordingStreamRuntime, record_cb) -> None:
    bc.stream = p.open(
        format=pyaudio.paInt16,
        channels=stream_runtime.num_of_channels,
        rate=stream_runtime.sr_ori,
        input=True,
        frames_per_buffer=stream_runtime.chunk_size,
        input_device_index=stream_runtime.input_device_index,
        stream_callback=record_cb,
    )


def prime_realtime_vad(
    ctx: RealtimeCallbackContext,
    resampled: bytes,
    *,
    get_speech_webrtc_fn=get_speech_webrtc,
    to_silero_fn=to_silero,
) -> None:
    if ctx.vad_checked:
        return

    ctx.vad_checked = True
    try:
        get_speech_webrtc_fn(resampled, WHISPER_SR, ctx.frame_duration_ms, ctx.webrtc_vad)
    except Exception:
        pass
    try:
        sil_probe = to_silero_fn(resampled, ctx.num_of_channels, ctx.samp_width)
        if sil_probe.numel() >= 512:
            ctx.silero_vad(sil_probe, WHISPER_SR)
    except Exception:
        pass


def detect_realtime_speech(
    ctx: RealtimeCallbackContext,
    in_data: bytes,
    resampled: bytes,
    *,
    prime_realtime_vad_fn=prime_realtime_vad,
    get_db_fn=get_db,
    get_speech_webrtc_fn=get_speech_webrtc,
    to_silero_fn=to_silero,
) -> tuple[bool, bytes]:
    data_to_queue = resampled if not ctx.use_temp else in_data
    prime_realtime_vad_fn(ctx, resampled)

    if not ctx.threshold_enable:
        return True, data_to_queue

    db = get_db_fn(in_data)
    shared_state.last_db = db
    if db > ctx.max_db:
        ctx.max_db = db
    elif db < ctx.min_db:
        ctx.min_db = db

    is_speech = False
    if ctx.threshold_auto:
        try:
            is_speech = bool(get_speech_webrtc_fn(resampled, WHISPER_SR, ctx.frame_duration_ms, ctx.webrtc_vad))
            if is_speech and ctx.use_silero and not ctx.silero_disabled:
                sil_data = to_silero_fn(resampled, ctx.num_of_channels, ctx.samp_width)
                if sil_data.numel() >= 512:
                    conf = float(ctx.silero_vad(sil_data, WHISPER_SR).item())
                    is_speech = conf >= ctx.silero_min_conf
        except Exception:
            pass
    else:
        is_speech = db > ctx.threshold_db

    return is_speech, data_to_queue


def update_realtime_queue_state(ctx: RealtimeCallbackContext, *, is_speech: bool, data_to_queue: bytes) -> None:
    if is_speech:
        bc.data_queue.put(data_to_queue)
        ctx.was_recording = True
        if ctx.is_silence:
            ctx.is_silence = False
            ctx.silence_started_at = 0.0
        return

    bc.current_rec_status = "▶️ Recording (Waiting for speech)"
    if ctx.was_recording:
        ctx.was_recording = False
        if not ctx.is_silence:
            ctx.is_silence = True
            ctx.silence_started_at = time()


def handle_record_callback_error(ctx: RealtimeCallbackContext | None, exc: Exception) -> None:
    message = str(exc)
    if "Input audio chunk is too short" not in message:
        logger.error(f"record_cb error: {message}")
    if ctx and "Error while processing frame" in message:
        if ctx.frame_duration_ms >= 20:
            ctx.frame_duration_ms -= 10
            ctx.vad_checked = False
        else:
            ctx.threshold_auto = False
