from __future__ import annotations

import os
from threading import Thread
from time import sleep
from typing import Dict, Optional, cast

from speech_translate.controller_protocols import JsonDict, ModelManagerBridge, SettingsStore, WhisperLoadApiGetter
from speech_translate.log_helpers import logger
from speech_translate.ui_protocol import TASK_SOURCE_MODEL_DOWNLOAD, TASK_SOURCE_MODEL_LOAD
from speech_translate.utils.whisper.download_runtime import TaskReporter
from speech_translate.utils.whisper.paths import get_default_download_root
from speech_translate.utils.whisper.helper import model_select_dict, model_values
from speech_translate.utils.types import SettingDict


def _get_whisper_download_api():
    from speech_translate.utils.whisper import download as whisper_download_api

    return whisper_download_api


class ModelManagerController:
    """Owns model directory resolution, model status cache, downloads, and runtime model state."""

    def __init__(
        self,
        bridge: ModelManagerBridge,
        settings: SettingsStore,
        whisper_loader_getter: WhisperLoadApiGetter,
        whisper_download_getter=_get_whisper_download_api,
    ):
        self.bridge = bridge
        self.settings = settings
        self.whisper_loader_getter = whisper_loader_getter
        self.whisper_download_getter = whisper_download_getter
        self.model_status_cache: Dict[str, JsonDict] = {}
        self.model_download_running = False
        self.model_load_running = False
        self.runtime_model_key = self.normalize_model_key(str(settings.cache.get("model_f_import", "")))
        self.runtime_model_loaded = False
        self.runtime_model_message = "模型未预加载"
        self.runtime_model_started_at = 0.0
        self.model_manager_engine = "whisper"
        self.model_manager_model = "small"

    def _set_runtime_model_state(
        self,
        *,
        model_key: Optional[str] = None,
        loaded: bool,
        loading: bool,
        message: str,
    ) -> None:
        if model_key is not None:
            self.runtime_model_key = self.normalize_model_key(str(model_key))
        self.runtime_model_loaded = bool(loaded)
        self.model_load_running = bool(loading)
        self.runtime_model_message = str(message)
        if loading:
            if self.runtime_model_started_at <= 0:
                from time import time as _time

                self.runtime_model_started_at = _time()
        else:
            self.runtime_model_started_at = 0.0

    def _resolve_runtime_model_message(self, normalized_key: str, *, loaded: bool, message: Optional[str]) -> str:
        if message:
            return message
        return f"Model ready: {normalized_key}" if loaded else f"Loading model cache for {normalized_key}"

    def resolve_model_dir(self) -> str:
        configured = self.settings.cache.get("dir_model", "auto")
        return configured if configured != "auto" else get_default_download_root()

    def get_model_manager_keys(self) -> list[str]:
        base_models = ["tiny", "base", "small", "medium", "large-v1", "large-v2", "large-v3"]
        return [model if "large" in model else f"{model}.en" for model in base_models] + base_models

    def normalize_model_key(self, value: str) -> str:
        if value in model_select_dict:
            return model_select_dict[value]
        if value in model_values:
            return value
        for _display_name, model_key in model_select_dict.items():
            if model_key == value:
                return model_key
        return value

    def normalize_engine_name(self, value: str) -> str:
        return value

    def is_model_available_for_backend(self, model_key: str, backend: str, model_dir: str) -> bool:
        if backend == "faster-whisper":
            try:
                return self.whisper_download_getter().verify_model_faster_whisper(model_key, model_dir)
            except Exception:
                return False
        return os.path.exists(os.path.join(model_dir, f"{model_key}.pt"))

    def verify_model_status(self, engine: str, model_key: str, model_dir: str) -> tuple[bool, str]:
        try:
            whisper_download_api = self.whisper_download_getter()
            downloaded = (
                whisper_download_api.verify_model_faster_whisper(model_key, model_dir)
                if engine == "faster-whisper"
                else whisper_download_api.verify_model_whisper(model_key, model_dir)
            )
            return downloaded, ""
        except Exception as exc:
            return False, str(exc)

    def cache_model_status(
        self,
        engine: str,
        model_key: str,
        downloaded: bool,
        error: str = "",
        downloading: bool = False,
        progress: Optional[float] = None,
        speed: str = "",
    ) -> None:
        if progress is None:
            progress = 100.0 if downloaded else 0.0
        self.model_status_cache[f"{engine}:{model_key}"] = {
            "engine": engine,
            "model": model_key,
            "downloaded": downloaded,
            "error": error,
            "downloading": downloading,
            "progress": float(max(0.0, min(100.0, progress))),
            "speed": speed,
        }

    def clear_model_status_cache(self) -> None:
        self.model_status_cache.clear()

    def _normalize_engine_scope(self, engine: Optional[str]) -> str:
        engine_name = str(engine or self.model_manager_engine or "whisper").strip().lower()
        return engine_name if engine_name in {"whisper", "faster-whisper"} else "whisper"

    def _normalize_model_scope(self, model_key: Optional[str]) -> str:
        normalized = self.normalize_model_key(str(model_key or self.model_manager_model or "small"))
        return normalized if normalized in self.get_model_manager_keys() else "small"

    def _build_model_rows(self, engines: list[str], models: list[str]) -> list[JsonDict]:
        rows: list[JsonDict] = []
        for row_engine in engines:
            for model_key in models:
                cached = self.model_status_cache.get(f"{row_engine}:{model_key}")
                rows.append(
                    {
                        "model": model_key,
                        "engine": row_engine,
                        "downloaded": cached.get("downloaded") if cached else None,
                        "downloading": cached.get("downloading", False) if cached else False,
                        "progress": float(cached.get("progress", 0.0)) if cached else 0.0,
                        "speed": str(cached.get("speed", "")) if cached else "",
                        "error": cached.get("error", "") if cached else "",
                    }
                )
        return rows

    def _build_download_reporter(self) -> TaskReporter:
        return TaskReporter(
            reset_task_state=lambda _title: None,
            update_task_message=lambda message: self.bridge.update_task_message(message, source=TASK_SOURCE_MODEL_DOWNLOAD),
            update_task_progress=lambda value: self.bridge.update_task_progress(value, source=TASK_SOURCE_MODEL_DOWNLOAD),
            finish_task=lambda _message: None,
            update_task_error=lambda _message: None,
        )

    def build_model_manager_state(self, engine_hint: Optional[str] = None, include_both: bool = False) -> JsonDict:
        self.model_manager_engine = self._normalize_engine_scope(engine_hint)
        models = self.get_model_manager_keys()
        self.model_manager_model = self._normalize_model_scope(self.model_manager_model)
        rows = self._build_model_rows(["whisper", "faster-whisper"] if include_both else [self.model_manager_engine], models)

        return {
            "engine_options": ["whisper", "faster-whisper"],
            "model_options": models,
            "selected_engine": self.model_manager_engine,
            "selected_model": self.model_manager_model,
            "model_dir": self.resolve_model_dir(),
            "download_running": self.model_download_running,
            "view_scope": "both" if include_both else "selected",
            "rows": rows,
        }

    def build_runtime_model_state(self) -> JsonDict:
        loaded = bool(self.runtime_model_loaded)
        elapsed = 0.0
        if self.model_load_running and self.runtime_model_started_at > 0:
            from time import time as _time

            elapsed = max(0.0, _time() - self.runtime_model_started_at)
        return {
            "key": self.runtime_model_key,
            "loading": bool(self.model_load_running) and not loaded,
            "loaded": loaded,
            "message": self.runtime_model_message,
            "elapsed_seconds": elapsed,
        }

    def get_model_manager_state(self, engine: Optional[str] = None) -> JsonDict:
        if engine is not None:
            self.model_manager_engine = str(engine)
        return self.build_model_manager_state(engine)

    def get_runtime_model_state(self) -> JsonDict:
        return self.build_runtime_model_state()

    def mark_runtime_model_pending(self, model_key: str, *, loaded: bool = False, message: Optional[str] = None) -> None:
        normalized_key = self.normalize_model_key(str(model_key))
        self._set_runtime_model_state(
            model_key=normalized_key,
            loaded=bool(loaded),
            loading=not bool(loaded),
            message=self._resolve_runtime_model_message(
                normalized_key,
                loaded=bool(loaded),
                message=message,
            ),
        )

    def mark_runtime_model_ready(self, model_key: Optional[str] = None, *, message: Optional[str] = None) -> None:
        normalized_key = self.normalize_model_key(str(model_key or self.runtime_model_key))
        self._set_runtime_model_state(
            model_key=normalized_key,
            loaded=True,
            loading=False,
            message=self._resolve_runtime_model_message(
                normalized_key,
                loaded=True,
                message=message,
            ),
        )

    def mark_runtime_model_failed(self, message: str) -> None:
        self._set_runtime_model_state(
            loaded=False,
            loading=False,
            message=str(message),
        )

    def check_model(self, model_key: str, engine: str = "whisper") -> JsonDict:
        self.model_manager_engine = self._normalize_engine_scope(engine)
        self.model_manager_model = self._normalize_model_scope(model_key)

        downloaded, error = self.verify_model_status(self.model_manager_engine, self.model_manager_model, self.resolve_model_dir())
        self.cache_model_status(self.model_manager_engine, self.model_manager_model, downloaded, error, downloading=False)
        state = self.build_model_manager_state(self.model_manager_engine)
        state["checked"] = {
            "model": self.model_manager_model,
            "engine": self.model_manager_engine,
            "downloaded": downloaded,
            "error": error,
        }
        return state

    def check_all_models(self, engine: str = "whisper") -> JsonDict:
        engine = str(engine or "whisper").strip().lower()
        engine = engine if engine in {"whisper", "faster-whisper", "both"} else "whisper"
        if engine != "both":
            self.model_manager_engine = self._normalize_engine_scope(engine)

        model_dir = self.resolve_model_dir()
        for target_engine in (["whisper", "faster-whisper"] if engine == "both" else [engine]):
            for model_key in self.get_model_manager_keys():
                downloaded, error = self.verify_model_status(target_engine, model_key, model_dir)
                self.cache_model_status(target_engine, model_key, downloaded, error, downloading=False)

        return self.build_model_manager_state(self.model_manager_engine, include_both=(engine == "both"))

    def download_model(self, model_key: str, engine: str = "whisper") -> JsonDict:
        engine = self._normalize_engine_scope(engine)
        if self.model_download_running:
            return {"ok": False, "message": "Another download is running"}

        self.model_manager_engine = engine
        self.model_manager_model = self._normalize_model_scope(model_key)

        def worker():
            self.model_download_running = True
            try:
                whisper_download_api = self.whisper_download_getter()
                model_dir = self.resolve_model_dir()
                os.makedirs(model_dir, exist_ok=True)
                self.bridge.reset_task_state("Model Download")
                self.bridge.update_task_message(f"Preparing download for {self.model_manager_model} ({engine})", source=TASK_SOURCE_MODEL_DOWNLOAD)
                self.bridge.update_task_progress(5, source=TASK_SOURCE_MODEL_DOWNLOAD)

                self.cache_model_status(engine, self.model_manager_model, False, downloading=True, progress=5, speed="-")

                success = whisper_download_api.download_model(
                    self.model_manager_model,
                    use_faster_whisper=(engine == "faster-whisper"),
                    download_root=model_dir,
                    reporter=self._build_download_reporter(),
                    progress_floor=5.0,
                    progress_ceiling=90.0,
                    progress_callback=lambda snapshot: self.cache_model_status(
                        engine,
                        self.model_manager_model,
                        False,
                        downloading=True,
                        progress=snapshot.progress,
                        speed=snapshot.speed_text,
                    ),
                )
                if not success:
                    raise RuntimeError("Download failed")

                self.bridge.update_task_progress(90, source=TASK_SOURCE_MODEL_DOWNLOAD)
                downloaded = False
                error = ""
                for _ in range(8):
                    if downloaded := self.verify_model_status(engine, self.model_manager_model, model_dir)[0]:
                        break
                    sleep(0.5)

                self.cache_model_status(
                    engine,
                    self.model_manager_model,
                    downloaded,
                    error,
                    downloading=False,
                    progress=100.0 if downloaded else 0.0,
                    speed="-",
                )
                if not downloaded:
                    raise RuntimeError(error or "Verification failed")

                self.bridge.update_task_progress(100, source=TASK_SOURCE_MODEL_DOWNLOAD)
                self.bridge.finish_task(f"Model downloaded: {self.model_manager_model} ({engine})")
            except Exception as exc:
                logger.exception(exc)
                self.cache_model_status(engine, self.model_manager_model, False, str(exc), downloading=False)
                self.bridge.update_task_error(str(exc))
            finally:
                self.model_download_running = False

        Thread(target=worker, daemon=True).start()
        return {"ok": True, "message": "Model download started", "model": self.model_manager_model, "engine": engine}

    def load_runtime_model(self, model_key: str) -> JsonDict:
        model_key = self._normalize_model_scope(model_key)
        if self.model_load_running:
            return {"ok": False, "message": "Another load is running"}

        self.settings.save_key("model_mw", model_key)
        self.settings.save_key("model_f_import", model_key)
        self.mark_runtime_model_pending(model_key)

        def worker():
            try:
                whisper_load_api = self.whisper_loader_getter()
                settings_snapshot = cast(SettingDict, self.bridge.get_settings_snapshot())
                settings_snapshot["model_mw"] = settings_snapshot["model_f_import"] = model_key
                engine = self.normalize_engine_name(str(settings_snapshot.get("tl_engine_mw", "Google Translate")))
                transcribe_enabled = bool(settings_snapshot.get("transcribe_mw", True))
                translate_enabled = bool(settings_snapshot.get("translate_mw", True))
                tl_engine_whisper = engine in model_values

                self.bridge.reset_task_state("Model Load")
                self.bridge.update_task_message(f"Loading model cache for {model_key}", source=TASK_SOURCE_MODEL_LOAD)
                self.bridge.update_task_progress(5, source=TASK_SOURCE_MODEL_LOAD)
                self.bridge.update_task_message(
                    f"Preparing model arguments for {model_key}",
                    source=TASK_SOURCE_MODEL_LOAD,
                )

                model_args = whisper_load_api.get_model_args(settings_snapshot)
                self.bridge.update_task_progress(15, source=TASK_SOURCE_MODEL_LOAD)
                self.bridge.update_task_message(
                    f"Checking model cache for {model_key}",
                    source=TASK_SOURCE_MODEL_LOAD,
                )

                bundle_cached = whisper_load_api.is_model_bundle_cached(
                    transcribe_enabled,
                    translate_enabled,
                    tl_engine_whisper,
                    model_key,
                    engine,
                    settings_snapshot,
                    **model_args,
                )
                if bundle_cached:
                    self.bridge.update_task_progress(80, source=TASK_SOURCE_MODEL_LOAD)
                    self.bridge.update_task_message(
                        f"Using cached runtime bundle for {model_key}",
                        source=TASK_SOURCE_MODEL_LOAD,
                    )
                else:
                    self.bridge.update_task_progress(35, source=TASK_SOURCE_MODEL_LOAD)
                    self.bridge.update_task_message(
                        f"Loading model into runtime memory for {model_key}",
                        source=TASK_SOURCE_MODEL_LOAD,
                    )

                whisper_load_api.get_model(
                    transcribe_enabled,
                    translate_enabled,
                    tl_engine_whisper,
                    model_key,
                    engine,
                    settings_snapshot,
                    **model_args,
                )

                self.bridge.update_task_progress(100, source=TASK_SOURCE_MODEL_LOAD)
                self.bridge.finish_task(f"Model ready: {model_key}")
                self.mark_runtime_model_ready(model_key)
            except Exception as exc:
                logger.exception(exc)
                self.bridge.update_task_error(str(exc))
                self.mark_runtime_model_failed(f"Model load failed: {exc}")
            finally:
                self.model_load_running = False

        Thread(target=worker, daemon=True).start()
        return {"ok": True, "message": "Model loading started", "model": model_key}

    def handle_task_message(self, message: str, source: str = "general") -> None:
        source_text = str(source or "general").strip().lower()
        text = str(message or "").strip()
        if not text:
            return

        if source_text == "model-download":
            return

        lowered = text.lower()
        if source_text == "model-load":
            if lowered.startswith("preparing model arguments for"):
                self.mark_runtime_model_pending(self.runtime_model_key, message=text)
                return
            if lowered.startswith("checking model cache for"):
                self.mark_runtime_model_pending(self.runtime_model_key, message=text)
                return
            if lowered.startswith("using cached runtime bundle for"):
                self.mark_runtime_model_pending(self.runtime_model_key, message=text)
                return
            if lowered.startswith("loading model into runtime memory for"):
                self.mark_runtime_model_pending(self.runtime_model_key, message=text)
                return
            if lowered.startswith("loading model") or lowered.startswith("loading model cache for"):
                if ":" in text:
                    candidate = text.split(":", 1)[1].strip()
                elif lowered.startswith("loading model cache for "):
                    candidate = text[len("Loading model cache for ") :].strip()
                else:
                    candidate = self.runtime_model_key
                self.mark_runtime_model_pending(candidate or self.runtime_model_key)
                return
            if lowered.startswith("model ready:") or lowered.startswith("model loaded:"):
                candidate = text.split(":", 1)[1].strip() if ":" in text else self.runtime_model_key
                self.mark_runtime_model_ready(candidate or self.runtime_model_key)
                return
            if lowered.startswith("model load failed"):
                self.mark_runtime_model_failed(text)
                return

        if lowered.startswith("loading model and preparing pipeline"):
            if not self.runtime_model_loaded:
                self.mark_runtime_model_pending(self.runtime_model_key)
            else:
                self.mark_runtime_model_ready(self.runtime_model_key)
            return
        if lowered.startswith("loading model:") or lowered.startswith("loading model cache for"):
            candidate = text.split(":", 1)[1].strip() if ":" in text else ""
            next_key = self.normalize_model_key(candidate) if candidate else self.runtime_model_key
            if self.runtime_model_loaded and next_key and self.runtime_model_key == next_key:
                self.mark_runtime_model_ready(self.runtime_model_key)
            else:
                self.mark_runtime_model_pending(next_key)
            return
        if lowered.startswith("model loaded:") or lowered.startswith("model ready:"):
            ready_key = self.normalize_model_key(text.split(":", 1)[1].strip() if ":" in text else self.runtime_model_key)
            self.mark_runtime_model_ready(ready_key)
            return
        if lowered.startswith("model load failed"):
            self.mark_runtime_model_failed(text)

    def handle_recording_status(self, payload: JsonDict) -> None:
        status_text = str(payload.get("status", "")).lower()
        if "initializing" in status_text:
            if self.runtime_model_key:
                self.mark_runtime_model_pending(self.runtime_model_key)
        elif any(fragment in status_text for fragment in ["recording", "transcrib", "translat"]):
            if self.runtime_model_key:
                self.mark_runtime_model_ready(self.runtime_model_key)
        elif "stopped" in status_text:
            self.model_load_running = False
