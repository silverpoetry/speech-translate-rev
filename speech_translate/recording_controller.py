from __future__ import annotations

from threading import Thread
from time import sleep, time
from typing import Any, Dict, Optional, cast

from loguru import logger

from speech_translate.linker import bc
from speech_translate.utils.types import SettingDict
from speech_translate.utils.whisper.helper import model_keys, model_values


DEFAULT_RECORDING_STATE: Dict[str, Any] = {
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


class RecordingSessionController:
    """Owns recording session lifecycle, runtime state, and worker orchestration."""

    def __init__(self, bridge: Any, whisper_loader_getter: Any, shutdown_selenium_fn: Any):
        self.bridge = bridge
        self.whisper_loader_getter = whisper_loader_getter
        self.shutdown_selenium_fn = shutdown_selenium_fn
        self.record_worker_thread: Optional[Thread] = None
        self.recording_state: Dict[str, Any] = dict(DEFAULT_RECORDING_STATE)

    def wait_recording_idle(self, timeout_s: float = 12.0) -> bool:
        start_t = time()
        while time() - start_t < timeout_s:
            worker_alive = self.record_worker_thread is not None and self.record_worker_thread.is_alive()
            stream_released = getattr(bc, "stream", None) is None
            recording_flag_off = not getattr(bc, "recording", False)
            if stream_released and recording_flag_off and not worker_alive:
                return True
            sleep(0.05)
        return False

    def set_recording_state(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self.bridge._lock:
            self.recording_state.update(payload)
            if "active" not in payload:
                self.recording_state["active"] = bool(bc.recording)
        self.bridge.model_manager_controller.handle_recording_status(payload)
        self.bridge._emit_ui_update(["task"])
        return {"ok": True}

    def get_recording_state(self) -> Dict[str, Any]:
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
    ) -> Dict[str, Any]:
        from speech_translate.utils.audio.record import record_session

        if bc.recording:
            return {"ok": False, "message": "Already recording"}

        settings_snapshot = self.bridge.get_settings_snapshot()
        lang_source = str(settings_snapshot.get("source_lang_mw", lang_source))
        lang_target = str(settings_snapshot.get("target_lang_mw", lang_target))
        device = str(settings_snapshot.get("input", device))
        engine = self.bridge._normalize_engine_name(str(settings_snapshot.get("tl_engine_mw", engine)))
        is_tc = bool(settings_snapshot.get("transcribe_mw", is_tc))
        is_tl = bool(settings_snapshot.get("translate_mw", is_tl))
        model_name_tc = self.bridge._normalize_model_key(str(settings_snapshot.get("model_mw", "")))
        self.bridge.model_manager_controller.mark_runtime_model_pending(model_name_tc)

        if not is_tc and not is_tl:
            return {"ok": False, "message": "Please enable Transcribe or Translate"}

        cached_bundle = False
        try:
            whisper_load_api = self.whisper_loader_getter()
            cached_bundle = whisper_load_api.is_model_bundle_cached(
                is_tc,
                is_tl,
                engine in model_values,
                model_name_tc,
                engine,
                cast(SettingDict, settings_snapshot),
                **whisper_load_api.get_model_args(cast(SettingDict, settings_snapshot)),
            )
        except Exception:
            pass

        if cached_bundle:
            self.bridge.model_manager_controller.mark_runtime_model_ready(model_name_tc)

        self.bridge.bind_headless_main_window()
        bc.tc_sentences = []
        bc.tl_sentences = []
        self.bridge.clear_live()
        bc.enable_rec()

        self.bridge.reset_task_state("Recording")
        self.set_recording_state(
            {
                "status": "Preparing recording..." if cached_bundle else "Initializing recording...",
                "active": True,
                "device": device,
                "lang_source": lang_source,
                "lang_target": lang_target,
                "engine": engine,
                "mode": "Transcribe & Translate" if is_tc and is_tl else "Transcribe" if is_tc else "Translate",
                "timer": "00:00:00",
                "buffer": "0/0 sec",
                "sentences": "0",
            }
        )

        import speech_translate.utils.audio.record as record_module

        record_module.mbox = lambda *args, **kwargs: True

        def worker():
            try:
                record_session(lang_source, lang_target, engine, model_name_tc, device, is_tc, is_tl, device.lower() == "speaker")
                self.bridge.finish_task("Recording finished")
            except Exception as exc:
                logger.exception(exc)
                self.bridge.update_task_error(str(exc))
            finally:
                bc.disable_rec()
                self.set_recording_state({"status": "Stopped", "active": False})
                if bool(self.bridge.get_settings_snapshot().get("selenium_auto_close_on_task_done", True)) and is_tl and engine == "Selenium Chrome Translate":
                    self.shutdown_selenium_fn()
                self.record_worker_thread = None

        self.record_worker_thread = Thread(target=worker, daemon=True)
        self.record_worker_thread.start()
        return {"ok": True, "device": device, "engine_whisper": engine in model_keys, "message": "Recording started"}

    def stop_recording(self) -> Dict[str, Any]:
        if not bc.recording:
            return {"ok": False, "message": "Not currently recording"}
        self.set_recording_state({"status": "Stopping...", "active": False})
        bc.disable_rec()

        if self.wait_recording_idle(timeout_s=12.0):
            self.set_recording_state({"status": "Stopped", "active": False})
            if bool(self.bridge.get_settings_snapshot().get("selenium_auto_close_on_task_done", True)) and self.bridge._normalize_engine_name(str(self.bridge.get_settings_snapshot().get("tl_engine_mw", ""))) == "Selenium Chrome Translate":
                self.shutdown_selenium_fn()
            return {"ok": True, "message": "Recording stopped"}
        return {"ok": True, "message": "Stop requested; cleanup is still finishing in background"}
