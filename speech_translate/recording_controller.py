from __future__ import annotations

from ast import literal_eval
from dataclasses import dataclass
from shlex import quote
from threading import RLock, Thread
from time import sleep, time
from typing import Optional

from speech_translate._constants import MAX_THRESHOLD, MIN_THRESHOLD
from speech_translate.controller_settings import RecordingControllerSettings, build_recording_controller_settings
from speech_translate.controller_protocols import (
    JsonDict,
    ModelManagerControllerApi,
    RecordingBridge,
    ShutdownSeleniumFn,
    WhisperLoadApiGetter,
)
from speech_translate.log_helpers import logger
from speech_translate.ui_protocol import UI_SECTION_TASK
from speech_translate.utils.helper import str_separator_to_html
from speech_translate.utils.audio.recording_runtime_state import (
    RecordingRuntimeStateAdapter,
    RecordingTextStoreAdapter,
    build_recording_runtime_state_adapter,
    build_recording_text_store_adapter,
)
from speech_translate.utils.audio.record_types import RecordingSessionDependencies, RecordingSessionRequest, RealtimeSharedState


DEFAULT_RECORDING_STATE: JsonDict = {
    "status": "Idle",
    "active": False,
    "device": "-",
    "lang_source": "-",
    "lang_target": "-",
    "engine": "-",
    "mode": "-",
    "timer": "00:00:00",
    "buffer": "0/0 sec",
    "sentences": "0",
    "last_db": None,
    "threshold_db": None,
}


@dataclass(frozen=True)
class RecordingStartContext:
    settings: RecordingControllerSettings

    @property
    def device(self) -> str:
        return self.settings.device

    @property
    def lang_source(self) -> str:
        return self.settings.lang_source

    @property
    def lang_target(self) -> str:
        return self.settings.lang_target

    @property
    def engine(self) -> str:
        return self.settings.engine

    @property
    def model_name_tc(self) -> str:
        return self.settings.model_name_tc

    @property
    def is_tc(self) -> bool:
        return self.settings.is_tc

    @property
    def is_tl(self) -> bool:
        return self.settings.is_tl

    @property
    def settings_snapshot(self):
        return self.settings.snapshot

    @property
    def mode(self) -> str:
        return "Transcribe & Translate" if self.is_tc and self.is_tl else "Transcribe" if self.is_tc else "Translate"

    @property
    def engine_is_whisper(self) -> bool:
        return self.settings.engine_is_whisper

    @property
    def should_auto_close_selenium(self) -> bool:
        return self.settings.should_auto_close_selenium


class RecordingSessionController:
    """Owns recording session lifecycle, runtime state, and worker orchestration."""

    def __init__(
        self,
        bridge: RecordingBridge,
        whisper_loader_getter: WhisperLoadApiGetter,
        shutdown_selenium_fn: ShutdownSeleniumFn,
        model_manager: ModelManagerControllerApi,
        runtime_state: RecordingRuntimeStateAdapter | None = None,
        text_store: RecordingTextStoreAdapter | None = None,
    ):
        self.bridge = bridge
        self.whisper_loader_getter = whisper_loader_getter
        self.shutdown_selenium_fn = shutdown_selenium_fn
        self.model_manager = model_manager
        self.runtime_state = runtime_state or build_recording_runtime_state_adapter()
        self.text_store = text_store or build_recording_text_store_adapter()
        self._lock = RLock()
        self.record_worker_thread: Optional[Thread] = None
        self.recording_state: JsonDict = dict(DEFAULT_RECORDING_STATE)
        self._shared_runtime_state = RealtimeSharedState()

    def wait_recording_idle(self, timeout_s: float = 12.0) -> bool:
        start_t = time()
        while time() - start_t < timeout_s:
            if self._is_recording_idle():
                return True
            sleep(0.05)
        return False

    def _is_recording_idle(self) -> bool:
        worker_alive = self.record_worker_thread is not None and self.record_worker_thread.is_alive()
        stream_released = self.runtime_state.is_stream_released()
        recording_flag_off = not self.runtime_state.is_recording_active()
        return stream_released and recording_flag_off and not worker_alive

    def _resolve_start_context(
        self,
        *,
        device: str,
        lang_source: str,
        lang_target: str,
        engine: str,
        is_tc: bool,
        is_tl: bool,
    ) -> RecordingStartContext:
        return RecordingStartContext(
            settings=build_recording_controller_settings(
                self.bridge.get_settings_snapshot(),
                default_device=device,
                default_lang_source=lang_source,
                default_lang_target=lang_target,
                default_engine=engine,
                default_is_tc=is_tc,
                default_is_tl=is_tl,
                normalize_engine_name=self.model_manager.normalize_engine_name,
                normalize_model_key=self.model_manager.normalize_model_key,
            )
        )

    def _probe_cached_bundle(self, context: RecordingStartContext) -> bool:
        try:
            whisper_load_api = self.whisper_loader_getter()
            return whisper_load_api.is_model_bundle_cached(
                context.is_tc,
                context.is_tl,
                context.engine_is_whisper,
                context.model_name_tc,
                context.engine,
                context.settings_snapshot,
                **whisper_load_api.get_model_args(context.settings_snapshot),
            )
        except Exception:
            return False

    def _build_recording_state(self, context: RecordingStartContext, *, cached_bundle: bool) -> JsonDict:
        return {
            "status": "Preparing recording..." if cached_bundle else "Initializing recording...",
            "active": True,
            "device": context.device,
            "lang_source": context.lang_source,
            "lang_target": context.lang_target,
            "engine": context.engine,
            "mode": context.mode,
            "timer": "00:00:00",
            "buffer": "0/0 sec",
            "sentences": "0",
            "last_db": None,
            "threshold_db": self._resolve_threshold_db(context),
        }

    def _resolve_threshold_db(self, context: RecordingStartContext) -> float:
        default_key = "threshold_db_speaker" if context.device.lower() == "speaker" else "threshold_db_mic"
        try:
            return float(context.settings_snapshot.get(default_key, -20.0))
        except Exception:
            return -20.0

    def _snapshot_recording_meter_state(self) -> JsonDict:
        last_db = self._shared_runtime_state.last_db
        if last_db is not None:
            try:
                last_db = max(float(MIN_THRESHOLD), min(float(MAX_THRESHOLD), float(last_db)))
            except Exception:
                last_db = None
        threshold_db = self.recording_state.get("threshold_db")
        try:
            threshold_db = None if threshold_db is None else float(threshold_db)
        except Exception:
            threshold_db = None
        return {
            "last_db": last_db,
            "threshold_db": threshold_db,
        }

    def _live_separator_html(self) -> str:
        raw_separator = str(self.bridge.get_settings_snapshot().get("separate_with", "\\n"))
        try:
            return str_separator_to_html(literal_eval(quote(raw_separator)))
        except Exception:
            return str_separator_to_html(raw_separator)

    @staticmethod
    def _has_live_result(value: object | None) -> bool:
        return bool(str(getattr(value, "text", value or "")).strip())

    def _reset_recording_runtime(self) -> None:
        self.text_store.set_transcribed_sentences([])
        self.text_store.set_translated_sentences([])
        self.bridge.clear_live()
        self.runtime_state.enable_recording()
        self._shared_runtime_state = RealtimeSharedState()
        self.bridge.reset_task_state("Recording")

    def _shutdown_recording_session(self, *, context: RecordingStartContext) -> None:
        self.runtime_state.disable_recording()
        self.set_recording_state({"status": "Stopped", "active": False})
        if context.should_auto_close_selenium:
            self.shutdown_selenium_fn()
        self.record_worker_thread = None

    def _build_recording_session_request(self, context: RecordingStartContext) -> RecordingSessionRequest:
        return RecordingSessionRequest(
            lang_source=context.lang_source,
            lang_target=context.lang_target,
            engine=context.engine,
            model_name_tc=context.model_name_tc,
            device=context.device,
            is_tc=context.is_tc,
            is_tl=context.is_tl,
            speaker=context.device.lower() == "speaker",
        )

    def _build_recording_session_dependencies(self, context: RecordingStartContext) -> RecordingSessionDependencies:
        from speech_translate.utils.audio.record_session_api import build_recording_session_control
        from speech_translate.utils.audio.record_runtime import build_recording_text_state
        from speech_translate.utils.audio.record_streaming import build_callback_context_store

        return RecordingSessionDependencies(
            settings_snapshot=dict(context.settings_snapshot),
            session_control=build_recording_session_control(runtime_state=self.runtime_state),
            runtime_text_state=build_recording_text_state(
                shared_runtime_state=self._shared_runtime_state,
                text_store=self.text_store,
            ),
            callback_context_store=build_callback_context_store(),
        )

    def _start_recording_worker(self, context: RecordingStartContext) -> None:
        from speech_translate.utils.audio.record_session_api import record_session
        request = self._build_recording_session_request(context)
        session_dependencies = self._build_recording_session_dependencies(context)

        def worker() -> None:
            try:
                record_session(request, dependencies=session_dependencies)
                self.bridge.finish_task("Recording finished")
            except Exception as exc:
                logger.exception(exc)
                self.bridge.update_task_error(str(exc))
            finally:
                self._shutdown_recording_session(context=context)

        self.record_worker_thread = Thread(target=worker, daemon=True)
        self.record_worker_thread.start()

    def set_recording_state(self, payload: JsonDict) -> JsonDict:
        with self._lock:
            self.recording_state.update(payload)
            if "active" not in payload:
                self.recording_state["active"] = self.runtime_state.is_recording_active()
        self.model_manager.handle_recording_status(payload)
        self.bridge.emit_ui_update([UI_SECTION_TASK])
        return {"ok": True}

    def get_recording_state(self) -> JsonDict:
        with self._lock:
            return {
                **self.recording_state,
                **self._snapshot_recording_meter_state(),
            }

    def rerender_live_text(self) -> JsonDict:
        separator = self._live_separator_html()
        previous_tc = self._shared_runtime_state.prev_tc_res
        previous_tl = self._shared_runtime_state.prev_tl_res
        has_tc = bool(self.text_store.transcribed_sentences()) or self._has_live_result(previous_tc)
        has_tl = bool(self.text_store.translated_sentences()) or self._has_live_result(previous_tl)

        if has_tc:
            self.text_store.update_transcribed_output(previous_tc if self._has_live_result(previous_tc) else None, separator)
        if has_tl:
            self.text_store.update_translated_output(previous_tl if self._has_live_result(previous_tl) else None, separator)

        return {"ok": True, "transcribed": has_tc, "translated": has_tl}

    def start_recording(
        self,
        device: str = "mic",
        lang_source: str = "English",
        lang_target: str = "Indonesian",
        engine: str = "Selenium Chrome Translate",
        is_tc: bool = True,
        is_tl: bool = True,
    ) -> JsonDict:
        if self.runtime_state.is_recording_active():
            return {"ok": False, "message": "Already recording"}

        context = self._resolve_start_context(
            device=device,
            lang_source=lang_source,
            lang_target=lang_target,
            engine=engine,
            is_tc=is_tc,
            is_tl=is_tl,
        )

        if not context.is_tc and not context.is_tl:
            return {"ok": False, "message": "Please enable Transcribe or Translate"}

        self.model_manager.mark_runtime_model_pending(context.model_name_tc)
        cached_bundle = self._probe_cached_bundle(context)

        if cached_bundle:
            self.model_manager.mark_runtime_model_ready(context.model_name_tc)

        self._reset_recording_runtime()
        self.set_recording_state(self._build_recording_state(context, cached_bundle=cached_bundle))
        self._start_recording_worker(context)
        return {"ok": True, "device": context.device, "engine_whisper": context.engine_is_whisper, "message": "Recording started"}

    def stop_recording(self) -> JsonDict:
        if not self.runtime_state.is_recording_active():
            return {"ok": False, "message": "Not currently recording"}
        self.set_recording_state({"status": "Stopping...", "active": False})
        self.runtime_state.disable_recording()

        if self.wait_recording_idle(timeout_s=12.0):
            settings = build_recording_controller_settings(
                self.bridge.get_settings_snapshot(),
                default_device=str(self.recording_state.get("device", "mic")),
                default_lang_source=str(self.recording_state.get("lang_source", "English")),
                default_lang_target=str(self.recording_state.get("lang_target", "Indonesian")),
                default_engine=str(self.recording_state.get("engine", "")),
                default_is_tc=True,
                default_is_tl=True,
                normalize_engine_name=self.model_manager.normalize_engine_name,
                normalize_model_key=self.model_manager.normalize_model_key,
            )
            if settings.should_auto_close_selenium:
                self.shutdown_selenium_fn()
            self.set_recording_state({"status": "Stopped", "active": False})
            return {"ok": True, "message": "Recording stopped"}
        return {"ok": True, "message": "Stop requested; cleanup is still finishing in background"}
