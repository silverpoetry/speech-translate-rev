from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Callable, Mapping

from speech_translate._constants import WHISPER_SR
from speech_translate.runtime_deps import get_whisper_to_language_code
from speech_translate.utils.audio.record_runtime import (
    BufferStateReducer,
    RecordingStatusEmitter,
    TranslationDispatcher,
    build_recording_text_state,
)
from speech_translate.utils.audio.record_settings import build_recording_model_settings
from speech_translate.utils.audio.record_types import (
    RecordingModelRuntime,
    RecordingRuntime,
    RecordingSessionBootstrap,
    RecordingSessionConfig,
    RecordingSessionLifecycle,
    RecordingSessionServices,
    RecordingStreamRuntime,
    RealtimeSessionState,
)
from speech_translate.utils.audio.recording_runtime_state import (
    RecordingRuntimeStateAdapter,
    build_recording_runtime_state_adapter,
)
from speech_translate.utils.translate.language import get_whisper_lang_similar


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


def build_recording_model_runtime(
    *,
    config: RecordingSessionConfig,
    lang_source: str,
    model_name_tc: str,
    engine: str,
    is_tc: bool,
    is_tl: bool,
    settings_snapshot: Mapping[str, object],
    get_model_fn: Callable[..., tuple[object, object, object, object, object]],
    get_model_args_fn: Callable[..., dict[str, object]],
    get_tc_args_fn: Callable[..., dict[str, object]],
    get_hallucination_filter_fn: Callable[..., dict[str, object]],
    initial_prompt_picker: Callable[[object, bool, object, object], str | None] | None,
) -> RecordingModelRuntime:
    model_settings = build_recording_model_settings(settings_snapshot)
    model_args = get_model_args_fn(model_settings.snapshot)
    _, _, stable_tc, stable_tl, to_args = get_model_fn(
        is_tc,
        is_tl,
        config.tl_engine_whisper,
        model_name_tc,
        engine,
        model_settings.snapshot,
        **model_args,
    )
    whisper_args = get_tc_args_fn(to_args, model_settings.snapshot)
    whisper_args["verbose"] = None
    configured_whisper_language = get_whisper_lang_similar(lang_source) if not config.auto else None
    whisper_args["language"] = get_whisper_to_language_code().get(configured_whisper_language) if configured_whisper_language else None

    if model_settings.enable_initial_prompt:
        prompt = (
            initial_prompt_picker(whisper_args.get("language"), True, model_settings.initial_prompts_map, None)
            if initial_prompt_picker is not None
            else None
        )
        if prompt:
            whisper_args["initial_prompt"] = prompt
        else:
            whisper_args.pop("initial_prompt", None)
    else:
        whisper_args.pop("initial_prompt", None)

    demucs_enabled = bool(whisper_args.get("demucs", False))
    vad_enabled = bool(whisper_args.get("vad", False))
    use_temp = config.use_temp
    if model_settings.use_faster_whisper and not use_temp:
        whisper_args["input_sr"] = WHISPER_SR
    if demucs_enabled and vad_enabled:
        use_temp = True

    hallucination_filters = (
        get_hallucination_filter_fn("rec", model_settings.path_filter_rec)
        if model_settings.filter_rec
        else {}
    )
    return RecordingModelRuntime(
        stable_tc=stable_tc,
        stable_tl=stable_tl,
        whisper_args=whisper_args,
        configured_whisper_language=configured_whisper_language,
        demucs_enabled=demucs_enabled,
        hallucination_filters=hallucination_filters,
        cuda_device=str(model_args["device"]),
        use_temp=use_temp,
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
    shared_runtime_state,
    callback_context_store_instance,
    build_config_fn: Callable[..., RecordingSessionConfig],
    load_model_runtime_fn: Callable[..., RecordingModelRuntime],
    build_stream_runtime_fn: Callable[..., RecordingStreamRuntime],
) -> RecordingSessionBootstrap:
    config = build_config_fn(
        rec_type=rec_type,
        lang_source=lang_source,
        engine=engine,
        is_tc=is_tc,
        is_tl=is_tl,
        settings_snapshot=settings_snapshot,
    )
    model_runtime = load_model_runtime_fn(
        config=config,
        lang_source=lang_source,
        model_name_tc=model_name_tc,
        engine=engine,
        is_tc=is_tc,
        is_tl=is_tl,
        settings_snapshot=settings_snapshot,
    )
    config.use_temp = model_runtime.use_temp
    stream_runtime = build_stream_runtime_fn(
        rec_type=rec_type,
        config=config,
        p=p,
        settings_snapshot=settings_snapshot,
        shared_runtime_state=shared_runtime_state,
        callback_context_store_instance=callback_context_store_instance,
    )
    return RecordingSessionBootstrap(
        config=config,
        model_runtime=model_runtime,
        stream_runtime=stream_runtime,
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
    runtime_text_state=None,
    status_emitter_factory: Callable[[RecordingRuntime], RecordingStatusEmitter] = RecordingStatusEmitter,
    translator_factory: Callable[..., TranslationDispatcher] = TranslationDispatcher,
    buffer_reducer_factory: Callable[..., BufferStateReducer] = BufferStateReducer,
    build_text_state_fn: Callable[[], object] = build_recording_text_state,
) -> RecordingSessionServices:
    runtime_text_state = runtime_text_state or build_text_state_fn()
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
    status_emitter = status_emitter_factory(runtime)
    translator = translator_factory(
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
    buffer_reducer = buffer_reducer_factory(
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
    runtime_text_state=None,
    build_services_fn: Callable[..., RecordingSessionServices],
    build_text_state_fn: Callable[[], object] = build_recording_text_state,
    lock_factory: Callable[[], object] = Lock,
) -> RecordingSessionLifecycle:
    runtime_text_state = runtime_text_state or build_text_state_fn()
    session_state = RealtimeSessionState()
    control.set_current_status("▶️ Recording (Waiting for speech)")
    runtime_text_state.set_detected_language("~")
    runtime_text_state.set_transcribed_sentences([])
    runtime_text_state.set_translated_sentences([])
    runtime_text_state.set_previous_transcribed_result("")
    runtime_text_state.set_previous_translated_result("")
    session_state.transcription_lock = lock_factory() if (is_tc and is_tl and config.tl_engine_whisper) else None

    services = build_services_fn(
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


def start_recording_session_support_threads(
    *,
    services: RecordingSessionServices,
    session_state: RealtimeSessionState,
    t_start: float,
    max_buffer_s: int,
    max_sentences: int,
    sentence_limitless: bool,
    control: RecordingSessionControl,
    runtime_text_state=None,
    start_translation_dispatcher_thread_fn: Callable[..., None],
    start_recording_status_thread_fn: Callable[..., None],
) -> None:
    start_translation_dispatcher_thread_fn(services.translator, control=control)
    services.update_status()
    start_recording_status_thread_fn(
        session_state,
        services.status_emitter,
        t_start=t_start,
        max_buffer_s=max_buffer_s,
        max_sentences=max_sentences,
        sentence_limitless=sentence_limitless,
        control=control,
        runtime_text_state=runtime_text_state,
    )


__all__ = [
    "RecordingSessionControl",
    "build_recording_model_runtime",
    "build_recording_session_control",
    "build_recording_session_services",
    "initialize_recording_session_lifecycle",
    "prepare_recording_session_bootstrap",
    "start_recording_session_support_threads",
]
