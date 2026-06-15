from __future__ import annotations

from dataclasses import dataclass
from threading import Thread
from time import sleep, time
from typing import Optional, cast

from loguru import logger

from speech_translate.controller_protocols import JsonDict, RecordingBridge, ShutdownSeleniumFn, WhisperLoadApiGetter
from speech_translate.linker import bc
from speech_translate.ui_protocol import UI_SECTION_TASK
from speech_translate.utils.types import SettingDict
from speech_translate.utils.whisper.helper import model_keys


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
}


@dataclass(frozen=True)
class RecordingStartContext:
    device: str
    lang_source: str
    lang_target: str
    engine: str
    model_name_tc: str
    is_tc: bool
    is_tl: bool
    settings_snapshot: SettingDict

    @property
    def mode(self) -> str:
        return "Transcribe & Translate" if self.is_tc and self.is_tl else "Transcribe" if self.is_tc else "Translate"

    @property
    def engine_is_whisper(self) -> bool:
        return self.engine in model_keys

    @property
    def should_auto_close_selenium(self) -> bool:
        return self.is_tl and self.engine == "Selenium Chrome Translate"


class RecordingSessionController:
    """Owns recording session lifecycle, runtime state, and worker orchestration."""

    def __init__(
        self,
        bridge: RecordingBridge,
        whisper_loader_getter: WhisperLoadApiGetter,
        shutdown_selenium_fn: ShutdownSeleniumFn,
    ):
        self.bridge = bridge
        self.whisper_loader_getter = whisper_loader_getter
        self.shutdown_selenium_fn = shutdown_selenium_fn
        self.record_worker_thread: Optional[Thread] = None
        self.recording_state: JsonDict = dict(DEFAULT_RECORDING_STATE)

    def wait_recording_idle(self, timeout_s: float = 12.0) -> bool:
        start_t = time()
        while time() - start_t < timeout_s:
            if self._is_recording_idle():
                return True
            sleep(0.05)
        return False

    def _is_recording_idle(self) -> bool:
        worker_alive = self.record_worker_thread is not None and self.record_worker_thread.is_alive()
        stream_released = getattr(bc, "stream", None) is None
        recording_flag_off = not getattr(bc, "recording", False)
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
        settings_snapshot = cast(SettingDict, self.bridge.get_settings_snapshot())
        return RecordingStartContext(
            device=str(settings_snapshot.get("input", device)),
            lang_source=str(settings_snapshot.get("source_lang_mw", lang_source)),
            lang_target=str(settings_snapshot.get("target_lang_mw", lang_target)),
            engine=self.bridge._normalize_engine_name(str(settings_snapshot.get("tl_engine_mw", engine))),
            model_name_tc=self.bridge._normalize_model_key(str(settings_snapshot.get("model_mw", ""))),
            is_tc=bool(settings_snapshot.get("transcribe_mw", is_tc)),
            is_tl=bool(settings_snapshot.get("translate_mw", is_tl)),
            settings_snapshot=settings_snapshot,
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
        }

    def _reset_recording_runtime(self) -> None:
        self.bridge.bind_headless_main_window()
        bc.tc_sentences = []
        bc.tl_sentences = []
        self.bridge.clear_live()
        bc.enable_rec()
        self.bridge.reset_task_state("Recording")

    def _shutdown_recording_session(self, *, context: RecordingStartContext) -> None:
        bc.disable_rec()
        self.set_recording_state({"status": "Stopped", "active": False})
        if context.should_auto_close_selenium and bool(self.bridge.get_settings_snapshot().get("selenium_auto_close_on_task_done", True)):
            self.shutdown_selenium_fn()
        self.record_worker_thread = None

    def _start_recording_worker(self, context: RecordingStartContext) -> None:
        from speech_translate.utils.audio.record import record_session

        def worker() -> None:
            try:
                record_session(
                    context.lang_source,
                    context.lang_target,
                    context.engine,
                    context.model_name_tc,
                    context.device,
                    context.is_tc,
                    context.is_tl,
                    context.device.lower() == "speaker",
                )
                self.bridge.finish_task("Recording finished")
            except Exception as exc:
                logger.exception(exc)
                self.bridge.update_task_error(str(exc))
            finally:
                self._shutdown_recording_session(context=context)

        self.record_worker_thread = Thread(target=worker, daemon=True)
        self.record_worker_thread.start()

    def set_recording_state(self, payload: JsonDict) -> JsonDict:
        with self.bridge._lock:
            self.recording_state.update(payload)
            if "active" not in payload:
                self.recording_state["active"] = bool(bc.recording)
        self.bridge.model_manager_controller.handle_recording_status(payload)
        self.bridge._emit_ui_update([UI_SECTION_TASK])
        return {"ok": True}

    def get_recording_state(self) -> JsonDict:
        with self.bridge._lock:
            return dict(self.recording_state)

    def start_recording(
        self,
        device: str = "mic",
        lang_source: str = "English",
        lang_target: str = "Indonesian",
        engine: str = "Selenium Chrome Translate",
        is_tc: bool = True,
        is_tl: bool = True,
    ) -> JsonDict:
        if bc.recording:
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

        self.bridge.model_manager_controller.mark_runtime_model_pending(context.model_name_tc)
        cached_bundle = self._probe_cached_bundle(context)

        if cached_bundle:
            self.bridge.model_manager_controller.mark_runtime_model_ready(context.model_name_tc)

        self._reset_recording_runtime()
        self.set_recording_state(self._build_recording_state(context, cached_bundle=cached_bundle))
        self._start_recording_worker(context)
        return {"ok": True, "device": context.device, "engine_whisper": context.engine in model_keys, "message": "Recording started"}

    def stop_recording(self) -> JsonDict:
        if not bc.recording:
            return {"ok": False, "message": "Not currently recording"}
        self.set_recording_state({"status": "Stopping...", "active": False})
        bc.disable_rec()

        if self.wait_recording_idle(timeout_s=12.0):
            if bool(self.bridge.get_settings_snapshot().get("selenium_auto_close_on_task_done", True)) and self.bridge._normalize_engine_name(str(self.bridge.get_settings_snapshot().get("tl_engine_mw", ""))) == "Selenium Chrome Translate":
                self.shutdown_selenium_fn()
            self.set_recording_state({"status": "Stopped", "active": False})
            return {"ok": True, "message": "Recording stopped"}
        return {"ok": True, "message": "Stop requested; cleanup is still finishing in background"}
