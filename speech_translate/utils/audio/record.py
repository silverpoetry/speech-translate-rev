import os
from ast import literal_eval
from datetime import UTC, datetime, timedelta
from platform import system
from queue import Empty, Queue
from shlex import quote
from threading import Lock, Thread
from time import gmtime, sleep, strftime, time

import numpy as np
import torch
import webrtcvad
from typing import Callable, cast
from whisper.tokenizer import TO_LANGUAGE_CODE

from speech_translate._constants import WHISPER_SR
from speech_translate._logging import logger
from speech_translate._path import dir_silero_vad, dir_temp
from speech_translate.linker import bc, sj
from speech_translate.utils.audio.audio import get_db, get_frame_duration, get_speech_webrtc, resample_sr, to_silero
from speech_translate.utils.audio.device import get_device_details
from speech_translate.utils.audio import record_processing as processing_module
from speech_translate.utils.audio.record_runtime import (
    BufferStateReducer,
    RecordingStatusEmitter,
    TranslationDispatcher,
    _build_full_transcribed_text,
    _build_recording_state_payload,
    _enforce_sentence_limits,
    _merge_translation_units,
    _normalize_translation_result_units,
    _resolve_live_input_source_language,
    _result_text,
    run_whisper_tl,
    shared_state,
    tl_api,
)
from speech_translate.utils.audio import record_streaming as streaming_module
from speech_translate.utils.audio.record_types import (
    AudioTarget,
    HallucinationFilters,
    LockLike,
    RecordingModelRuntime,
    RecordingRuntime,
    RecordingSessionConfig,
    RecordingSessionLifecycle,
    RecordingSessionServices,
    RecordingStreamRuntime,
    RealtimeCallbackContext,
    RealtimeSessionState,
    RealtimeSharedState,
    ResultSnapshot,
    SegmentLike,
    SileroVadLike,
    SmartSplitOutcome,
    TranscriptionResultLike,
    TranslationApiResult,
    TranslationTask,
    WhisperCallable,
)
from speech_translate.utils.translate.language import get_whisper_lang_name, get_whisper_lang_similar

from ..helper import str_separator_to_html
from ..whisper.helper import get_hallucination_filter, model_values
from ..whisper.load import get_model, get_model_args, get_tc_args
from ..whisper.result import remove_segments_by_str
if system() == "Windows":
    import pyaudiowpatch as pyaudio
else:
    import pyaudio


callback_context: RealtimeCallbackContext | None = None

_build_smart_split_outcome = processing_module.build_smart_split_outcome
# =========================================================================
# HELPER FUNCTIONS
# =========================================================================
def _build_recording_session_config(
    *,
    rec_type: str,
    lang_source: str,
    engine: str,
    is_tc: bool,
    is_tl: bool,
) -> RecordingSessionConfig:
    return RecordingSessionConfig(
        rec_type=rec_type,
        transcribe_rate=timedelta(seconds=sj.cache["transcribe_rate"] / 1000),
        max_buffer_s=int(sj.cache.get(f"max_buffer_{rec_type}", 10)),
        max_sentences=int(sj.cache.get(f"max_sentences_{rec_type}", 5)),
        sentence_limitless=bool(sj.cache.get(f"{rec_type}_no_limit", False)),
        tl_engine_whisper=engine in model_values,
        taskname="Transcribe & Translate" if is_tc and is_tl else "Transcribe" if is_tc else "Translate",
        auto=lang_source.lower() == "auto detect",
        threshold_enable=bool(sj.cache.get(f"threshold_enable_{rec_type}", True)),
        threshold_db=float(sj.cache.get(f"threshold_db_{rec_type}", -20)),
        threshold_auto=bool(sj.cache.get(f"threshold_auto_{rec_type}", True)),
        use_silero=bool(sj.cache.get(f"threshold_auto_silero_{rec_type}", True)),
        silero_min_conf=float(sj.cache.get(f"threshold_silero_{rec_type}_min", 0.75)),
        auto_break_buffer=bool(sj.cache.get(f"auto_break_buffer_{rec_type}", True)),
        use_temp=bool(sj.cache["use_temp"]),
        separator=str_separator_to_html(literal_eval(quote(sj.cache["separate_with"]))),
    )


def _load_recording_model_runtime(
    *,
    config: RecordingSessionConfig,
    lang_source: str,
    model_name_tc: str,
    engine: str,
    is_tc: bool,
    is_tl: bool,
) -> RecordingModelRuntime:
    model_args = get_model_args(sj.cache)
    _, _, stable_tc, stable_tl, to_args = get_model(
        is_tc,
        is_tl,
        config.tl_engine_whisper,
        model_name_tc,
        engine,
        sj.cache,
        **model_args,
    )
    whisper_args = get_tc_args(to_args, sj.cache)
    whisper_args["verbose"] = None
    configured_whisper_language = get_whisper_lang_similar(lang_source) if not config.auto else None
    whisper_args["language"] = TO_LANGUAGE_CODE.get(configured_whisper_language) if configured_whisper_language else None

    if sj.cache.get("enable_initial_prompt", False):
        from ..whisper.prompts import pick_initial_prompt

        prompt = pick_initial_prompt(whisper_args.get("language"), True, sj.cache.get("initial_prompts_map", {}), None)
        if prompt:
            whisper_args["initial_prompt"] = prompt
        else:
            whisper_args.pop("initial_prompt", None)
    else:
        whisper_args.pop("initial_prompt", None)

    demucs_enabled = bool(whisper_args.get("demucs", False))
    vad_enabled = bool(whisper_args.get("vad", False))
    use_temp = config.use_temp
    if sj.cache["use_faster_whisper"] and not use_temp:
        whisper_args["input_sr"] = WHISPER_SR
    if demucs_enabled and vad_enabled:
        use_temp = True

    hallucination_filters = get_hallucination_filter('rec', sj.cache["path_filter_rec"]) if sj.cache["filter_rec"] else {}
    return RecordingModelRuntime(
        stable_tc=cast(WhisperCallable | None, stable_tc),
        stable_tl=cast(WhisperCallable | None, stable_tl),
        whisper_args=whisper_args,
        configured_whisper_language=configured_whisper_language,
        demucs_enabled=demucs_enabled,
        hallucination_filters=hallucination_filters,
        cuda_device=str(model_args["device"]),
        use_temp=use_temp,
    )

def _drain_audio_queue() -> None:
    while not bc.data_queue.empty():
        bc.data_queue.get()


def _cleanup_temp_audio_paths(temp_audio_paths: list[str]) -> None:
    for audio in temp_audio_paths:
        try:
            os.remove(audio)
        except Exception:
            pass


def _cleanup_translation_audio(audio_target: AudioTarget | None) -> None:
    if isinstance(audio_target, str):
        try:
            os.remove(audio_target)
        except Exception:
            pass


def _build_recording_sentence_count_text(*, sentence_limitless: bool, max_sentences: int) -> str:
    sentence_count_text = f"{len(bc.tc_sentences) or len(bc.tl_sentences) or '0'}"
    if not sentence_limitless:
        sentence_count_text += f"/{max_sentences}"
    return sentence_count_text


def _run_recording_status_loop(
    session_state: RealtimeSessionState,
    status_emitter: RecordingStatusEmitter,
    *,
    t_start: float,
    max_buffer_s: int,
    max_sentences: int,
    sentence_limitless: bool,
) -> None:
    while bc.recording:
        if session_state.paused:
            sleep(0.1)
            continue
        try:
            status_emitter.emit(
                status=bc.current_rec_status,
                timer=strftime("%H:%M:%S", gmtime(time() - t_start)),
                buffer_text=f"{round(session_state.duration_seconds, 2)}/{round(max_buffer_s, 2)} sec",
                sentences=_build_recording_sentence_count_text(
                    sentence_limitless=sentence_limitless,
                    max_sentences=max_sentences,
                ),
            )
            sleep(0.1)
        except Exception:
            break


def _start_translation_dispatcher_thread(translator: TranslationDispatcher) -> None:
    Thread(
        target=lambda: translator.close(lambda: bool(bc.recording), _cleanup_translation_audio),
        daemon=True,
    ).start()


def _start_recording_status_thread(
    session_state: RealtimeSessionState,
    status_emitter: RecordingStatusEmitter,
    *,
    t_start: float,
    max_buffer_s: int,
    max_sentences: int,
    sentence_limitless: bool,
) -> None:
    Thread(
        target=lambda: _run_recording_status_loop(
            session_state,
            status_emitter,
            t_start=t_start,
            max_buffer_s=max_buffer_s,
            max_sentences=max_sentences,
            sentence_limitless=sentence_limitless,
        ),
        daemon=True,
    ).start()


def _cleanup_processed_audio_target(
    audio_target: AudioTarget,
    *,
    use_temp: bool,
    keep_temp: bool,
    is_tl: bool,
    tl_engine_whisper: bool,
    session_state: RealtimeSessionState,
) -> None:
    if not use_temp or keep_temp or not isinstance(audio_target, str):
        return
    if is_tl and tl_engine_whisper:
        return
    try:
        os.remove(audio_target)
        session_state.temp_audio_paths.remove(audio_target)
    except Exception:
        pass


def _execute_recording_iteration(
    *,
    audio_target: AudioTarget,
    session_state: RealtimeSessionState,
    is_tc: bool,
    is_tl: bool,
    config: RecordingSessionConfig,
    model_runtime: RecordingModelRuntime,
    translator: TranslationDispatcher,
) -> bool:
    if is_tl and config.tl_engine_whisper and not is_tc:
        bc.current_rec_status = "▶️ Recording ⟳ Translating Audio"
        translator.dispatch(audio_target, "")
        return True

    bc.current_rec_status = "▶️ Recording ⟳ Transcribing Audio"
    session_state.prev_tc_buffer_seconds = session_state.duration_seconds

    if model_runtime.stable_tc is None:
        return False

    result = _execute_realtime_transcription(audio_target, model_runtime.stable_tc, model_runtime.whisper_args)
    if result is None:
        return False

    result = _filter_realtime_transcription_result(
        result,
        hallucination_filters=model_runtime.hallucination_filters,
        auto=config.auto,
        configured_language=model_runtime.configured_whisper_language,
    )
    _commit_realtime_transcription(
        result,
        audio_target=audio_target,
        is_tl=is_tl,
        separator=config.separator,
        translator=translator,
    )
    return True


def _drain_pending_audio(session_state: RealtimeSessionState) -> None:
    while not bc.data_queue.empty():
        session_state.append_audio(bc.data_queue.get_nowait())


def _consume_record_loop_input(
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
) -> bytes | None:
    try:
        return bc.data_queue.get(timeout=0.1)
    except Empty:
        if (
            config.auto_break_buffer
            and callback_ctx.is_silence
            and time() - callback_ctx.silence_started_at > 1
        ):
            callback_ctx.is_silence = False
            _break_buffer_and_update_state(
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
            )
            bc.current_rec_status = "▶️ Recording (Waiting for speech)"
        if (
            not session_state.last_sample
            or (
                session_state.next_transcribe_time
                and session_state.next_transcribe_time > _utc_now()
            )
        ):
            return None
        return b""


def _advance_recording_buffer(
    session_state: RealtimeSessionState,
    data: bytes,
    *,
    transcribe_rate: timedelta,
    samp_width: int,
    num_of_channels: int,
    sr_divider: int,
    min_input_length: float,
) -> bool:
    now = _utc_now()
    if not session_state.next_transcribe_time:
        session_state.next_transcribe_time = now + transcribe_rate

    session_state.append_audio(data)
    _drain_pending_audio(session_state)

    if session_state.next_transcribe_time > now:
        return False
    session_state.next_transcribe_time = now + transcribe_rate

    session_state.recalculate_duration(
        samp_width=samp_width,
        num_of_channels=num_of_channels,
        sr_divider=sr_divider,
    )
    return session_state.duration_seconds >= min_input_length


def _finalize_recording_session(
    p,
    session_state: RealtimeSessionState | None,
    update_status_lbl: Callable[[], None] | None,
    *,
    keep_temp: bool,
) -> None:
    if update_status_lbl is not None:
        bc.current_rec_status = "⚠️ Stopping stream"
        update_status_lbl()
    if bc.stream:
        bc.stream.stop_stream()
        bc.stream.close()
        bc.stream = None
    bc.rec_tc_thread = bc.rec_tl_thread = None

    if update_status_lbl is not None:
        bc.current_rec_status = "⚠️ Terminating pyaudio"
        update_status_lbl()
    p.terminate()

    _drain_audio_queue()
    if session_state is not None and not keep_temp:
        _cleanup_temp_audio_paths(session_state.temp_audio_paths)

    _reset_callback_context()
    bc.current_rec_status = "⏹️ Stopped"
    if update_status_lbl is not None:
        update_status_lbl()


def _run_recording_session_loop(
    *,
    lifecycle: RecordingSessionLifecycle,
    config: RecordingSessionConfig,
    model_runtime: RecordingModelRuntime,
    is_tc: bool,
    is_tl: bool,
    rec_type: str,
) -> None:
    while bc.recording:
        if lifecycle.session_state.paused:
            sleep(0.1)
            continue

        data = _consume_record_loop_input(
            lifecycle.session_state,
            lifecycle.callback_ctx,
            config=config,
            is_tc=is_tc,
            sr_divider=lifecycle.sr_divider,
            samp_width=lifecycle.samp_width,
            num_of_channels=lifecycle.num_of_channels,
            translator=lifecycle.services.translator,
            buffer_reducer=lifecycle.services.buffer_reducer,
        )
        if data is None:
            continue

        if not _advance_recording_buffer(
            lifecycle.session_state,
            data,
            transcribe_rate=config.transcribe_rate,
            samp_width=lifecycle.samp_width,
            num_of_channels=lifecycle.num_of_channels,
            sr_divider=lifecycle.sr_divider,
            min_input_length=sj.cache.get(f"min_input_length_{rec_type}", 0.4),
        ):
            continue

        audio_target = _build_record_audio_target(
            lifecycle.session_state,
            use_temp=config.use_temp,
            num_of_channels=lifecycle.num_of_channels,
            samp_width=lifecycle.samp_width,
            demucs_enabled=model_runtime.demucs_enabled,
            cuda_device=model_runtime.cuda_device,
            sr_ori=lifecycle.sr_ori,
        )

        if not _execute_recording_iteration(
            audio_target=audio_target,
            session_state=lifecycle.session_state,
            is_tc=is_tc,
            is_tl=is_tl,
            config=config,
            model_runtime=model_runtime,
            translator=lifecycle.services.translator,
        ):
            continue

        _cleanup_processed_audio_target(
            audio_target,
            use_temp=config.use_temp,
            keep_temp=lifecycle.services.runtime.keep_temp,
            is_tl=is_tl,
            tl_engine_whisper=config.tl_engine_whisper,
            session_state=lifecycle.session_state,
        )

        if lifecycle.session_state.duration_seconds > config.max_buffer_s:
            _break_buffer_and_update_state(
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
            )
        if bc.current_rec_status == "▶️ Recording ⟳ Transcribing Audio":
            bc.current_rec_status = "▶️ Recording"


def _calculate_buffer_duration(
    audio_bytes: bytes,
    *,
    samp_width: int,
    num_of_channels: int,
    sr_divider: int,
) -> float:
    if samp_width <= 0 or num_of_channels <= 0 or sr_divider <= 0:
        return 0.0
    return len(audio_bytes) / (samp_width * num_of_channels * sr_divider)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _get_callback_context() -> RealtimeCallbackContext | None:
    return streaming_module.get_callback_context()


def _reset_callback_context() -> None:
    global callback_context
    streaming_module.reset_callback_context()
    callback_context = streaming_module.callback_context


def _initialize_callback_context(
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
    callback_context = streaming_module.initialize_callback_context(
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
    )
    return callback_context


def _load_recording_vad_runtime(*, rec_type: str) -> tuple[webrtcvad.Vad, SileroVadLike]:
    return streaming_module.load_recording_vad_runtime(rec_type=rec_type)


def _build_recording_stream_runtime(
    *,
    rec_type: str,
    config: RecordingSessionConfig,
    p,
) -> RecordingStreamRuntime:
    success, detail = get_device_details(rec_type, sj, p)
    if not success:
        raise Exception("Failed to get device details")

    device_detail = cast(dict[str, object], detail["device_detail"])
    sr_ori = int(detail["sample_rate"])
    num_of_channels = int(detail["num_of_channels"])
    chunk_size = int(detail["chunk_size"])

    if not sj.cache["supress_record_warning"] and sr_ori > 48000:
        logger.warning(f"Sample rate is high ({sr_ori} Hz). May cause issues. Can be suppressed in settings.")

    webrtc_vad, silero_vad = _load_recording_vad_runtime(rec_type=rec_type)
    samp_width = p.get_sample_size(pyaudio.paInt16)
    sr_divider = WHISPER_SR if not config.use_temp else sr_ori
    callback_ctx = _initialize_callback_context(
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


def _open_recording_stream(*, p, stream_runtime: RecordingStreamRuntime) -> None:
    streaming_module.open_recording_stream(p=p, stream_runtime=stream_runtime, record_cb=record_cb)


def _build_recording_session_services(
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
) -> RecordingSessionServices:
    runtime = RecordingRuntime(
        taskname=config.taskname,
        device=device,
        lang_source=lang_source,
        lang_target=lang_target,
        engine=engine,
        is_tl=is_tl,
        use_temp=config.use_temp,
        separator=config.separator,
        keep_temp=bool(sj.cache.get("keep_temp", False)),
        t_start=t_start,
        max_buffer_s=config.max_buffer_s,
        max_sentences=config.max_sentences,
        sentence_limitless=config.sentence_limitless,
        lang_target_display=lang_target if is_tl else "-",
    )
    status_emitter = RecordingStatusEmitter(runtime)
    translator = TranslationDispatcher(
        is_tl=is_tl,
        tl_engine_whisper=config.tl_engine_whisper,
        use_temp=config.use_temp,
        keep_temp=runtime.keep_temp,
        separator=config.separator,
        lang_source=lang_source,
        lang_target=lang_target,
        engine=engine,
        hallucination_filters=model_runtime.hallucination_filters,
        stable_tl=model_runtime.stable_tl,
        whisper_args=model_runtime.whisper_args,
        record_status_updater=lambda: status_emitter.emit(status=bc.current_rec_status),
    )
    buffer_reducer = BufferStateReducer(
        is_tc=is_tc,
        is_tl=is_tl,
        tl_engine_whisper=config.tl_engine_whisper,
        sentence_limitless=config.sentence_limitless,
        max_sentences=config.max_sentences,
        separator=config.separator,
        translator=translator,
    )
    return RecordingSessionServices(
        runtime=runtime,
        status_emitter=status_emitter,
        translator=translator,
        buffer_reducer=buffer_reducer,
    )


def _initialize_recording_session_lifecycle(
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
) -> RecordingSessionLifecycle:
    session_state = RealtimeSessionState()
    bc.current_rec_status, bc.auto_detected_lang = "▶️ Recording (Waiting for speech)", "~"
    bc.tc_sentences, bc.tl_sentences = [], []
    shared_state.prev_tc_res, shared_state.prev_tl_res = "", ""
    bc.tc_lock = Lock() if (is_tc and is_tl and config.tl_engine_whisper) else None

    services = _build_recording_session_services(
        config=config,
        model_runtime=model_runtime,
        device=device,
        lang_source=lang_source,
        lang_target=lang_target,
        engine=engine,
        is_tc=is_tc,
        is_tl=is_tl,
        t_start=t_start,
    )
    return RecordingSessionLifecycle(
        session_state=session_state,
        services=services,
        callback_ctx=stream_runtime.callback_ctx,
        sr_ori=stream_runtime.sr_ori,
        num_of_channels=stream_runtime.num_of_channels,
        samp_width=stream_runtime.samp_width,
        sr_divider=stream_runtime.sr_divider,
    )


def _start_recording_session_support_threads(
    *,
    services: RecordingSessionServices,
    session_state: RealtimeSessionState,
    t_start: float,
    max_buffer_s: int,
    max_sentences: int,
    sentence_limitless: bool,
) -> None:
    _start_translation_dispatcher_thread(services.translator)
    services.update_status()
    _start_recording_status_thread(
        session_state,
        services.status_emitter,
        t_start=t_start,
        max_buffer_s=max_buffer_s,
        max_sentences=max_sentences,
        sentence_limitless=sentence_limitless,
    )


def _prime_realtime_vad(ctx: RealtimeCallbackContext, resampled: bytes) -> None:
    if ctx.vad_checked:
        return

    ctx.vad_checked = True
    try:
        get_speech_webrtc(resampled, WHISPER_SR, ctx.frame_duration_ms, ctx.webrtc_vad)
    except Exception:
        pass
    try:
        sil_probe = to_silero(resampled, ctx.num_of_channels, ctx.samp_width)
        if sil_probe.numel() >= 512:
            ctx.silero_vad(sil_probe, WHISPER_SR)
    except Exception:
        pass


def _detect_realtime_speech(ctx: RealtimeCallbackContext, in_data: bytes, resampled: bytes) -> tuple[bool, bytes]:
    return streaming_module.detect_realtime_speech(ctx, in_data, resampled)


def _update_realtime_queue_state(ctx: RealtimeCallbackContext, *, is_speech: bool, data_to_queue: bytes) -> None:
    streaming_module.update_realtime_queue_state(ctx, is_speech=is_speech, data_to_queue=data_to_queue)


def _handle_record_callback_error(ctx: RealtimeCallbackContext | None, exc: Exception) -> None:
    streaming_module.handle_record_callback_error(ctx, exc)


def _build_record_audio_target(
    session_state: RealtimeSessionState,
    *,
    use_temp: bool,
    num_of_channels: int,
    samp_width: int,
    demucs_enabled: bool,
    cuda_device: str,
    sr_ori: int,
) -> AudioTarget:
    if not use_temp:
        wf = BytesIO()
        with w_open(wf, "wb") as wav_writer:
            wav_writer.setframerate(WHISPER_SR)
            wav_writer.setsampwidth(samp_width)
            wav_writer.setnchannels(num_of_channels)
            wav_writer.writeframes(session_state.last_sample)
        wf.seek(0)

        with w_open(wf, "rb") as wav_reader:
            audio_bytes = wav_reader.readframes(wav_reader.getnframes())
        return _bytes_to_numpy(audio_bytes, num_of_channels, demucs_enabled, cuda_device)

    audio_target = _save_to_temp(session_state.last_sample, num_of_channels, samp_width, sr_ori)
    session_state.temp_audio_paths.append(audio_target)
    return audio_target


def _execute_realtime_transcription(
    audio_target: AudioTarget,
    stable_tc: WhisperCallable,
    whisper_args: dict[str, object],
) -> TranscriptionResultLike | None:
    return processing_module.execute_realtime_transcription(audio_target, stable_tc, whisper_args)


def _filter_realtime_transcription_result(
    result: TranscriptionResultLike | None,
    *,
    hallucination_filters: dict[str, object],
    auto: bool,
    configured_language: str | None,
) -> TranscriptionResultLike | None:
    if not (sj.cache["filter_rec"] and result):
        return result

    try:
        filter_language = get_whisper_lang_name(result.language) if auto else configured_language
        if not filter_language:
            return result
        return remove_segments_by_str(
            result,
            hallucination_filters.get(filter_language, []),
            sj.cache["filter_rec_case_sensitive"],
            sj.cache["filter_rec_strip"],
            sj.cache["filter_rec_ignore_punctuations"],
            sj.cache["filter_rec_exact_match"],
            sj.cache["filter_rec_similarity"],
            sj.cache["debug_realtime_record"],
        )
    except Exception:
        return result


def _commit_realtime_transcription(
    result: TranscriptionResultLike | None,
    *,
    audio_target: AudioTarget,
    is_tl: bool,
    separator: str,
    translator: TranslationDispatcher,
) -> None:
    processing_module.commit_realtime_transcription(
        result,
        audio_target=audio_target,
        is_tl=is_tl,
        separator=separator,
        translator=translator,
    )


def _save_to_temp(audio_bytes: bytes, channels: int, samp_width: int, sr: int) -> str:
    return processing_module.save_to_temp(audio_bytes, channels, samp_width, sr)

def _bytes_to_numpy(audio_bytes: bytes, channels: int, use_demucs: bool, device: str) -> np.ndarray | torch.Tensor:
    return processing_module.bytes_to_numpy(audio_bytes, channels, use_demucs, device)

def _calculate_smart_split(
    segments: list,
    half_point_time: float,
) -> tuple[float | None, list[dict[str, object]], list[dict[str, object]]]:
    return processing_module.calculate_smart_split(segments, half_point_time)


def _apply_smart_split(
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
        utc_now=_utc_now,
    )


def _break_buffer_and_update_state(
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
        utc_now=_utc_now,
    )

# =========================================================================
# MAIN SESSION
# =========================================================================

def record_session(
    lang_source: str, lang_target: str, engine: str, model_name_tc: str, device: str, is_tc: bool, is_tl: bool, speaker: bool = False
) -> None:
    """实时录音、语音识别与翻译核心总管"""
    rec_type = "speaker" if speaker else "mic"
    p = None
    lifecycle: RecordingSessionLifecycle | None = None

    try:
        config = _build_recording_session_config(
            rec_type=rec_type,
            lang_source=lang_source,
            engine=engine,
            is_tc=is_tc,
            is_tl=is_tl,
        )
        p = pyaudio.PyAudio()

        model_runtime = _load_recording_model_runtime(
            config=config,
            lang_source=lang_source,
            model_name_tc=model_name_tc,
            engine=engine,
            is_tc=is_tc,
            is_tl=is_tl,
        )
        config.use_temp = model_runtime.use_temp
        stream_runtime = _build_recording_stream_runtime(rec_type=rec_type, config=config, p=p)

        logger.info(
            f"Session starting: {config.taskname} | Engine: {engine} | Device: {model_runtime.cuda_device} | Demucs: {model_runtime.demucs_enabled}"
        )

        t_start = time()
        lifecycle = _initialize_recording_session_lifecycle(
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
        )
        _start_recording_session_support_threads(
            services=lifecycle.services,
            session_state=lifecycle.session_state,
            t_start=t_start,
            max_buffer_s=config.max_buffer_s,
            max_sentences=config.max_sentences,
            sentence_limitless=config.sentence_limitless,
        )

        _open_recording_stream(p=p, stream_runtime=stream_runtime)

        # Main Transcribing Loop
        _run_recording_session_loop(
            lifecycle=lifecycle,
            config=config,
            model_runtime=model_runtime,
            is_tc=is_tc,
            is_tl=is_tl,
            rec_type=rec_type,
        )
    except Exception as e:
        logger.error(f"Error in record session: {str(e)}")
    finally:
        if p is not None:
            try:
                _finalize_recording_session(
                    p,
                    lifecycle.session_state if lifecycle is not None else None,
                    lifecycle.services.update_status if lifecycle is not None else None,
                    keep_temp=lifecycle.services.runtime.keep_temp if lifecycle is not None else True,
                )
            except Exception as finalize_exc:
                logger.error(f"Error finalizing record session: {finalize_exc}")
        torch.cuda.empty_cache()
        logger.info("Record session ended")


def record_cb(in_data, _frame_count, _time_info, _status):
    """Audio stream callback for PyAudio"""
    ctx = _get_callback_context()
    try:
        if ctx is None:
            return (in_data, pyaudio.paContinue)

        resampled = resample_sr(in_data, ctx.sample_rate, WHISPER_SR)
        is_speech, data_to_queue = _detect_realtime_speech(ctx, in_data, resampled)
        _update_realtime_queue_state(ctx, is_speech=is_speech, data_to_queue=data_to_queue)

        return (in_data, pyaudio.paContinue)
    except Exception as exc:
        _handle_record_callback_error(ctx, exc)
        return (in_data, pyaudio.paContinue)

# =========================================================================
# API / WORKER EXECUTORS
# =========================================================================

