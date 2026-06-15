import os
from ast import literal_eval
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from queue import Empty, Queue
from shlex import quote
from threading import Lock, Thread
from time import gmtime, sleep, strftime, time

from typing import Callable, Mapping, cast

from speech_translate._constants import WHISPER_SR
from speech_translate._logging import logger
from speech_translate.runtime_registry import settings_registry
from speech_translate.runtime_deps import empty_torch_cuda_cache, get_whisper_to_language_code
from speech_translate.utils.audio.audio import get_db, get_speech_webrtc, resample_sr, to_silero
from speech_translate.utils.audio.device import get_device_details, get_pyaudio_module
from speech_translate.utils.audio import record_processing as processing_module
from speech_translate.utils.audio.record_runtime import (
    BufferStateReducer,
    RecordingSettingsAdapter,
    RecordingStatusEmitter,
    RecordingTextState,
    TranslationDispatcher,
    _build_full_transcribed_text,
    _build_recording_state_payload,
    _enforce_sentence_limits,
    _merge_translation_units,
    _normalize_translation_result_units,
    _resolve_live_input_source_language,
    _result_text,
    build_recording_text_state,
    run_whisper_tl,
    shared_state,
    tl_api,
)
from speech_translate.utils.audio import record_streaming as streaming_module
from speech_translate.utils.audio.recording_runtime_state import (
    RecordingRuntimeStateAdapter,
    build_recording_runtime_state_adapter,
)
from speech_translate.utils.audio.record_streaming import CallbackContextStore, StreamingStateAdapter
from speech_translate.utils.audio.record_types import (
    AudioTarget,
    RecordingSessionBootstrap,
    RecordingSessionFinalizeContext,
    RecordingModelRuntime,
    RecordingRuntime,
    RecordingSessionConfig,
    RecordingSessionLifecycle,
    RecordingSessionServices,
    RecordingStreamRuntime,
    RealtimeCallbackContext,
    RealtimeSessionState,
    RealtimeSharedState,
    SileroVadLike,
    SmartSplitOutcome,
    TranscriptionResultLike,
    TranslationTask,
    WhisperCallable,
)
from speech_translate.utils.translate.language import get_whisper_lang_name, get_whisper_lang_similar

from ..helper import str_separator_to_html
from ..whisper.helper import get_hallucination_filter, model_values
from ..whisper.result import remove_segments_by_str

_build_smart_split_outcome = processing_module.build_smart_split_outcome


def _get_whisper_runtime_api():
    from speech_translate.utils.whisper import load as whisper_load_api

    return whisper_load_api


def get_model(*args, **kwargs):
    return _get_whisper_runtime_api().get_model(*args, **kwargs)


def get_model_args(*args, **kwargs):
    return _get_whisper_runtime_api().get_model_args(*args, **kwargs)


def get_tc_args(*args, **kwargs):
    return _get_whisper_runtime_api().get_tc_args(*args, **kwargs)


def _get_recording_settings_store() -> RecordingSettingsAdapter:
    return RecordingSettingsAdapter(cache=settings_registry.get().cache)


def _recording_settings_snapshot(settings_snapshot: Mapping[str, object] | None = None) -> Mapping[str, object]:
    return _get_recording_settings_store().cache if settings_snapshot is None else settings_snapshot


@dataclass
class RecordingSessionControl:
    runtime_state: RecordingRuntimeStateAdapter = field(default_factory=build_recording_runtime_state_adapter)

    def is_recording(self) -> bool:
        return self.runtime_state.is_recording_active()

    def current_status(self) -> str:
        return self.runtime_state.current_status()

    def set_current_status(self, status: str) -> None:
        self.runtime_state.set_current_status(status)

    def data_queue_empty(self) -> bool:
        return self.runtime_state.data_queue_empty()

    def get_data(self, *, timeout: float) -> bytes:
        return self.runtime_state.get_data(timeout=timeout)

    def get_data_nowait(self) -> bytes:
        return self.runtime_state.get_data_nowait()

    def clear_data_queue(self) -> None:
        self.runtime_state.clear_data_queue()

    def stream(self):
        return self.runtime_state.stream()

    def clear_stream(self) -> None:
        self.runtime_state.clear_stream()

    def clear_runtime_threads(self) -> None:
        self.runtime_state.clear_runtime_threads()


def build_recording_session_control(
    *,
    runtime_state: RecordingRuntimeStateAdapter | None = None,
) -> RecordingSessionControl:
    return RecordingSessionControl(runtime_state=runtime_state or build_recording_runtime_state_adapter())


recording_control = build_recording_session_control()


# Keep these wrappers in record.py because tests and external callers monkey-patch
# them directly. The real logic lives in record_processing / record_streaming.
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
    settings_snapshot: Mapping[str, object] | None = None,
) -> RecordingSessionConfig:
    settings_snapshot = _recording_settings_snapshot(settings_snapshot)
    return RecordingSessionConfig(
        rec_type=rec_type,
        transcribe_rate=timedelta(seconds=settings_snapshot["transcribe_rate"] / 1000),
        max_buffer_s=int(settings_snapshot.get(f"max_buffer_{rec_type}", 10)),
        max_sentences=int(settings_snapshot.get(f"max_sentences_{rec_type}", 5)),
        sentence_limitless=bool(settings_snapshot.get(f"{rec_type}_no_limit", False)),
        min_input_length=float(settings_snapshot.get(f"min_input_length_{rec_type}", 0.4)),
        keep_temp=bool(settings_snapshot.get("keep_temp", False)),
        tl_engine_whisper=engine in model_values,
        taskname="Transcribe & Translate" if is_tc and is_tl else "Transcribe" if is_tc else "Translate",
        auto=lang_source.lower() == "auto detect",
        threshold_enable=bool(settings_snapshot.get(f"threshold_enable_{rec_type}", True)),
        threshold_db=float(settings_snapshot.get(f"threshold_db_{rec_type}", -20)),
        threshold_auto=bool(settings_snapshot.get(f"threshold_auto_{rec_type}", True)),
        use_silero=bool(settings_snapshot.get(f"threshold_auto_silero_{rec_type}", True)),
        silero_min_conf=float(settings_snapshot.get(f"threshold_silero_{rec_type}_min", 0.75)),
        auto_break_buffer=bool(settings_snapshot.get(f"auto_break_buffer_{rec_type}", True)),
        use_temp=bool(settings_snapshot["use_temp"]),
        separator=str_separator_to_html(literal_eval(quote(settings_snapshot["separate_with"]))),
    )


def _prepare_recording_session_bootstrap(
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
) -> RecordingSessionBootstrap:
    config = _build_recording_session_config(
        rec_type=rec_type,
        lang_source=lang_source,
        engine=engine,
        is_tc=is_tc,
        is_tl=is_tl,
        settings_snapshot=settings_snapshot,
    )
    model_runtime = _load_recording_model_runtime(
        config=config,
        lang_source=lang_source,
        model_name_tc=model_name_tc,
        engine=engine,
        is_tc=is_tc,
        is_tl=is_tl,
        settings_snapshot=settings_snapshot,
    )
    config.use_temp = model_runtime.use_temp
    stream_runtime = _build_recording_stream_runtime(
        rec_type=rec_type,
        config=config,
        p=p,
        settings_snapshot=settings_snapshot,
        shared_runtime_state=shared_runtime_state or RealtimeSharedState(),
        callback_context_store_instance=callback_context_store_instance,
    )
    return RecordingSessionBootstrap(
        config=config,
        model_runtime=model_runtime,
        stream_runtime=stream_runtime,
    )


def _load_recording_model_runtime(
    *,
    config: RecordingSessionConfig,
    lang_source: str,
    model_name_tc: str,
    engine: str,
    is_tc: bool,
    is_tl: bool,
    settings_snapshot: Mapping[str, object] | None = None,
) -> RecordingModelRuntime:
    settings_snapshot = _recording_settings_snapshot(settings_snapshot)
    model_args = get_model_args(settings_snapshot)
    _, _, stable_tc, stable_tl, to_args = get_model(
        is_tc,
        is_tl,
        config.tl_engine_whisper,
        model_name_tc,
        engine,
        settings_snapshot,
        **model_args,
    )
    whisper_args = get_tc_args(to_args, settings_snapshot)
    whisper_args["verbose"] = None
    configured_whisper_language = get_whisper_lang_similar(lang_source) if not config.auto else None
    whisper_args["language"] = get_whisper_to_language_code().get(configured_whisper_language) if configured_whisper_language else None

    if settings_snapshot.get("enable_initial_prompt", False):
        from ..whisper.prompts import pick_initial_prompt

        prompt = pick_initial_prompt(whisper_args.get("language"), True, settings_snapshot.get("initial_prompts_map", {}), None)
        if prompt:
            whisper_args["initial_prompt"] = prompt
        else:
            whisper_args.pop("initial_prompt", None)
    else:
        whisper_args.pop("initial_prompt", None)

    demucs_enabled = bool(whisper_args.get("demucs", False))
    vad_enabled = bool(whisper_args.get("vad", False))
    use_temp = config.use_temp
    if settings_snapshot["use_faster_whisper"] and not use_temp:
        whisper_args["input_sr"] = WHISPER_SR
    if demucs_enabled and vad_enabled:
        use_temp = True

    hallucination_filters = get_hallucination_filter('rec', settings_snapshot["path_filter_rec"]) if settings_snapshot["filter_rec"] else {}
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

def _drain_audio_queue(control: RecordingSessionControl | None = None) -> None:
    (control or recording_control).clear_data_queue()


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


def _build_recording_sentence_count_text(
    *,
    sentence_limitless: bool,
    max_sentences: int,
    runtime_text_state: RecordingTextState | None = None,
) -> str:
    runtime_text_state = runtime_text_state or build_recording_text_state()
    sentence_count_text = f"{len(runtime_text_state.transcribed_sentences()) or len(runtime_text_state.translated_sentences()) or '0'}"
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
    control: RecordingSessionControl | None = None,
    runtime_text_state: RecordingTextState | None = None,
) -> None:
    control = control or recording_control
    runtime_text_state = runtime_text_state or build_recording_text_state()
    while control.is_recording():
        if session_state.paused:
            sleep(0.1)
            continue
        try:
            status_emitter.emit(
                status=control.current_status(),
                timer=strftime("%H:%M:%S", gmtime(time() - t_start)),
                buffer_text=f"{round(session_state.duration_seconds, 2)}/{round(max_buffer_s, 2)} sec",
                sentences=_build_recording_sentence_count_text(
                    sentence_limitless=sentence_limitless,
                    max_sentences=max_sentences,
                    runtime_text_state=runtime_text_state,
                ),
            )
            sleep(0.1)
        except Exception:
            break


def _start_translation_dispatcher_thread(
    translator: TranslationDispatcher,
    control: RecordingSessionControl | None = None,
) -> None:
    control = control or recording_control
    Thread(
        target=lambda: translator.close(control.is_recording, _cleanup_translation_audio),
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
    control: RecordingSessionControl | None = None,
    runtime_text_state: RecordingTextState | None = None,
) -> None:
    Thread(
        target=lambda: _run_recording_status_loop(
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
    control: RecordingSessionControl | None = None,
    runtime_text_state: RecordingTextState | None = None,
) -> bool:
    control = control or recording_control
    if is_tl and config.tl_engine_whisper and not is_tc:
        control.set_current_status("▶️ Recording ⟳ Translating Audio")
        translator.dispatch(audio_target, "")
        return True

    control.set_current_status("▶️ Recording ⟳ Transcribing Audio")
    session_state.prev_tc_buffer_seconds = session_state.duration_seconds

    if model_runtime.stable_tc is None:
        return False

    result = _execute_realtime_transcription(
        audio_target,
        model_runtime.stable_tc,
        model_runtime.whisper_args,
        transcription_lock=session_state.transcription_lock,
    )
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
        runtime_text_state=runtime_text_state,
        set_current_status=control.set_current_status,
    )
    return True


def _drain_pending_audio(
    session_state: RealtimeSessionState,
    control: RecordingSessionControl | None = None,
) -> None:
    control = control or recording_control
    while not control.data_queue_empty():
        session_state.append_audio(control.get_data_nowait())


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
    control: RecordingSessionControl | None = None,
    runtime_text_state: RecordingTextState | None = None,
) -> bytes | None:
    control = control or recording_control
    try:
        return control.get_data(timeout=0.1)
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
                runtime_text_state=runtime_text_state,
            )
            control.set_current_status("▶️ Recording (Waiting for speech)")
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
    control: RecordingSessionControl | None = None,
) -> bool:
    control = control or recording_control
    now = _utc_now()
    if not session_state.next_transcribe_time:
        session_state.next_transcribe_time = now + transcribe_rate

    session_state.append_audio(data)
    _drain_pending_audio(session_state, control=control)

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
    finalize_context: RecordingSessionFinalizeContext,
    control: RecordingSessionControl | None = None,
) -> None:
    control = control or recording_control
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

    _drain_audio_queue(control)
    if finalize_context.session_state is not None and not finalize_context.keep_temp:
        _cleanup_temp_audio_paths(finalize_context.session_state.temp_audio_paths)

    _reset_callback_context()
    control.set_current_status("⏹️ Stopped")
    if finalize_context.update_status is not None:
        finalize_context.update_status()


def _run_recording_session_loop(
    *,
    lifecycle: RecordingSessionLifecycle,
    config: RecordingSessionConfig,
    model_runtime: RecordingModelRuntime,
    is_tc: bool,
    is_tl: bool,
    rec_type: str,
    control: RecordingSessionControl | None = None,
    runtime_text_state: RecordingTextState | None = None,
) -> None:
    control = control or recording_control
    while control.is_recording():
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
            control=control,
            runtime_text_state=runtime_text_state,
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
            min_input_length=config.min_input_length,
            control=control,
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
            control=control,
            runtime_text_state=runtime_text_state,
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
                runtime_text_state=runtime_text_state,
            )
        if control.current_status() == "▶️ Recording ⟳ Transcribing Audio":
            control.set_current_status("▶️ Recording")


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
    streaming_module.reset_callback_context()


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


def _load_recording_vad_runtime(*, rec_type: str, settings_snapshot=None) -> tuple[object, SileroVadLike]:
    return streaming_module.load_recording_vad_runtime(rec_type=rec_type, settings_snapshot=settings_snapshot)


def _build_recording_stream_runtime(
    *,
    rec_type: str,
    config: RecordingSessionConfig,
    p,
    settings_snapshot: Mapping[str, object] | None = None,
    shared_runtime_state: RealtimeSharedState | None = None,
    callback_context_store_instance: CallbackContextStore | None = None,
) -> RecordingStreamRuntime:
    pyaudio = get_pyaudio_module()
    return streaming_module.build_recording_stream_runtime(
        rec_type=rec_type,
        config=config,
        p=p,
        get_device_details_fn=get_device_details,
        load_recording_vad_runtime_fn=_load_recording_vad_runtime,
        initialize_callback_context_fn=_initialize_callback_context,
        audio_format=pyaudio.paInt16,
        logger_instance=logger,
        settings_snapshot=_recording_settings_snapshot(settings_snapshot),
        shared_runtime_state=shared_runtime_state,
        callback_context_store_instance=callback_context_store_instance,
    )


def _open_recording_stream(
    *,
    p,
    stream_runtime: RecordingStreamRuntime,
    record_cb_override: Callable | None = None,
    state_adapter: StreamingStateAdapter | None = None,
) -> None:
    streaming_module.open_recording_stream(
        p=p,
        stream_runtime=stream_runtime,
        record_cb=record_cb if record_cb_override is None else record_cb_override,
        state_adapter=state_adapter,
    )


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
    control: RecordingSessionControl | None = None,
    runtime_text_state: RecordingTextState | None = None,
) -> RecordingSessionServices:
    control = control or recording_control
    runtime_text_state = runtime_text_state or build_recording_text_state()
    runtime = RecordingRuntime(
        taskname=config.taskname,
        device=device,
        lang_source=lang_source,
        lang_target=lang_target,
        engine=engine,
        is_tl=is_tl,
        use_temp=config.use_temp,
        separator=config.separator,
        keep_temp=config.keep_temp,
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
        record_status_updater=lambda: status_emitter.emit(status=control.current_status()),
        runtime_text_state=runtime_text_state,
    )
    buffer_reducer = BufferStateReducer(
        is_tc=is_tc,
        is_tl=is_tl,
        tl_engine_whisper=config.tl_engine_whisper,
        sentence_limitless=config.sentence_limitless,
        max_sentences=config.max_sentences,
        separator=config.separator,
        translator=translator,
        runtime_text_state=runtime_text_state,
    )
    return RecordingSessionServices(
        runtime=runtime,
        status_emitter=status_emitter,
        translator=translator,
        buffer_reducer=buffer_reducer,
        control=control,
        status_getter=control.current_status,
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
    control: RecordingSessionControl | None = None,
    runtime_text_state: RecordingTextState | None = None,
) -> RecordingSessionLifecycle:
    control = control or recording_control
    runtime_text_state = runtime_text_state or build_recording_text_state()
    session_state = RealtimeSessionState()
    control.set_current_status("▶️ Recording (Waiting for speech)")
    runtime_text_state.set_detected_language("~")
    runtime_text_state.set_transcribed_sentences([])
    runtime_text_state.set_translated_sentences([])
    runtime_text_state.set_previous_transcribed_result("")
    runtime_text_state.set_previous_translated_result("")
    session_state.transcription_lock = Lock() if (is_tc and is_tl and config.tl_engine_whisper) else None

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
        control=control,
        runtime_text_state=runtime_text_state,
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
    control: RecordingSessionControl | None = None,
    runtime_text_state: RecordingTextState | None = None,
) -> None:
    if control is None:
        _start_translation_dispatcher_thread(services.translator)
    else:
        _start_translation_dispatcher_thread(services.translator, control=control)
    services.update_status()
    _start_recording_status_thread(
        session_state,
        services.status_emitter,
        t_start=t_start,
        max_buffer_s=max_buffer_s,
        max_sentences=max_sentences,
        sentence_limitless=sentence_limitless,
        control=control,
        runtime_text_state=runtime_text_state,
    )


def _prime_realtime_vad(ctx: RealtimeCallbackContext, resampled: bytes) -> None:
    streaming_module.prime_realtime_vad(
        ctx,
        resampled,
        get_speech_webrtc_fn=get_speech_webrtc,
        to_silero_fn=to_silero,
    )


def _detect_realtime_speech(ctx: RealtimeCallbackContext, in_data: bytes, resampled: bytes) -> tuple[bool, bytes]:
    return streaming_module.detect_realtime_speech(
        ctx,
        in_data,
        resampled,
        prime_realtime_vad_fn=_prime_realtime_vad,
        get_db_fn=get_db,
        get_speech_webrtc_fn=get_speech_webrtc,
        to_silero_fn=to_silero,
    )


def _update_realtime_queue_state(
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


def _handle_record_callback_error(ctx: RealtimeCallbackContext | None, exc: Exception) -> None:
    streaming_module.handle_record_callback_error(ctx, exc)


def _execute_record_callback(
    in_data,
    _frame_count,
    _time_info,
    _status,
    *,
    callback_ctx: RealtimeCallbackContext | None,
    state_adapter: StreamingStateAdapter | None = None,
):
    pyaudio = get_pyaudio_module()
    try:
        if callback_ctx is None:
            return (in_data, pyaudio.paContinue)

        resampled = resample_sr(in_data, callback_ctx.sample_rate, WHISPER_SR)
        is_speech, data_to_queue = _detect_realtime_speech(callback_ctx, in_data, resampled)
        _update_realtime_queue_state(
            callback_ctx,
            is_speech=is_speech,
            data_to_queue=data_to_queue,
            state_adapter=state_adapter,
        )

        return (in_data, pyaudio.paContinue)
    except Exception as exc:
        _handle_record_callback_error(callback_ctx, exc)
        return (in_data, pyaudio.paContinue)


def build_record_callback(
    callback_ctx: RealtimeCallbackContext | None,
    *,
    state_adapter: StreamingStateAdapter | None = None,
):
    def _session_record_cb(in_data, frame_count, time_info, status):
        return _execute_record_callback(
            in_data,
            frame_count,
            time_info,
            status,
            callback_ctx=callback_ctx,
            state_adapter=state_adapter,
        )

    return _session_record_cb


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
    return processing_module.build_record_audio_target(
        session_state,
        use_temp=use_temp,
        num_of_channels=num_of_channels,
        samp_width=samp_width,
        demucs_enabled=demucs_enabled,
        cuda_device=cuda_device,
        sr_ori=sr_ori,
        save_to_temp_fn=_save_to_temp,
        bytes_to_numpy_fn=_bytes_to_numpy,
    )


def _execute_realtime_transcription(
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


def _filter_realtime_transcription_result(
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


def _commit_realtime_transcription(
    result: TranscriptionResultLike | None,
    *,
    audio_target: AudioTarget,
    is_tl: bool,
    separator: str,
    translator: TranslationDispatcher,
    runtime_text_state: RecordingTextState | None = None,
    set_current_status=None,
) -> None:
    processing_module.commit_realtime_transcription(
        result,
        audio_target=audio_target,
        is_tl=is_tl,
        separator=separator,
        translator=translator,
        runtime_text_state=runtime_text_state or build_recording_text_state(),
        set_current_status=set_current_status or recording_control.set_current_status,
    )


def _save_to_temp(audio_bytes: bytes, channels: int, samp_width: int, sr: int) -> str:
    return processing_module.save_to_temp(audio_bytes, channels, samp_width, sr)

def _bytes_to_numpy(audio_bytes: bytes, channels: int, use_demucs: bool, device: str) -> object:
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
        utc_now=_utc_now,
        runtime_text_state=runtime_text_state or build_recording_text_state(),
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
        utc_now=_utc_now,
        runtime_text_state=runtime_text_state or build_recording_text_state(),
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
    settings_snapshot = dict(_get_recording_settings_store().cache)
    session_shared_state = RealtimeSharedState()
    session_text_state = build_recording_text_state(shared_runtime_state=session_shared_state)
    session_control = build_recording_session_control()
    session_callback_context_store = streaming_module.build_callback_context_store()

    try:
        p = get_pyaudio_module().PyAudio()
        bootstrap = _prepare_recording_session_bootstrap(
            rec_type=rec_type,
            settings_snapshot=settings_snapshot,
            lang_source=lang_source,
            engine=engine,
            model_name_tc=model_name_tc,
            is_tc=is_tc,
            is_tl=is_tl,
            p=p,
            shared_runtime_state=session_shared_state,
            callback_context_store_instance=session_callback_context_store,
        )
        config = bootstrap.config
        model_runtime = bootstrap.model_runtime
        stream_runtime = bootstrap.stream_runtime

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
            control=session_control,
            runtime_text_state=session_text_state,
        )
        _start_recording_session_support_threads(
            services=lifecycle.services,
            session_state=lifecycle.session_state,
            t_start=t_start,
            max_buffer_s=config.max_buffer_s,
            max_sentences=config.max_sentences,
            sentence_limitless=config.sentence_limitless,
            control=session_control,
            runtime_text_state=session_text_state,
        )

        stream_state_adapter = StreamingStateAdapter(runtime_state=session_control.runtime_state)
        _open_recording_stream(
            p=p,
            stream_runtime=stream_runtime,
            record_cb_override=build_record_callback(
                stream_runtime.callback_ctx,
                state_adapter=stream_state_adapter,
            ),
            state_adapter=stream_state_adapter,
        )

        # Main Transcribing Loop
        _run_recording_session_loop(
            lifecycle=lifecycle,
            config=config,
            model_runtime=model_runtime,
            is_tc=is_tc,
            is_tl=is_tl,
            rec_type=rec_type,
            control=session_control,
            runtime_text_state=session_text_state,
        )
    except Exception as e:
        logger.error(f"Error in record session: {str(e)}")
    finally:
        if p is not None:
            try:
                finalize_context = RecordingSessionFinalizeContext.from_lifecycle(lifecycle)
                _finalize_recording_session(p, finalize_context, control=session_control)
            except Exception as finalize_exc:
                logger.error(f"Error finalizing record session: {finalize_exc}")
        session_callback_context_store.reset()
        empty_torch_cuda_cache()
        logger.info("Record session ended")


def record_cb(in_data, _frame_count, _time_info, _status):
    """Audio stream callback for PyAudio"""
    return _execute_record_callback(
        in_data,
        _frame_count,
        _time_info,
        _status,
        callback_ctx=_get_callback_context(),
    )

# =========================================================================
# API / WORKER EXECUTORS
# =========================================================================

