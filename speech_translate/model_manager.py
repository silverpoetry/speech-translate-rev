from __future__ import annotations

import os
from threading import Thread
from typing import Dict, Optional

from speech_translate.controller_protocols import JsonDict, ModelManagerBridge, SettingsStore, WhisperLoadApiGetter
from speech_translate.log_helpers import logger
from speech_translate.model_manager_runtime import RuntimeModelStateMachine, estimate_whisper_model_bytes
from speech_translate.ui_protocol import UI_SECTION_TASK
from speech_translate.model_manager_workflows import (
    ModelDownloadRequest,
    ModelDownloadService,
    RuntimeModelLoadRequest,
    RuntimeModelLoadService,
)
from speech_translate.utils.whisper.paths import get_default_download_root
from speech_translate.utils.whisper.helper import model_select_dict, model_values


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
        self._runtime_model = RuntimeModelStateMachine(
            normalize_model_key=self.normalize_model_key,
            key=self.normalize_model_key(str(settings.cache.get("model_mw", ""))),
        )
        self.model_manager_engine = "whisper"
        self.model_manager_model = "small"
        self.download_service = ModelDownloadService(
            bridge,
            whisper_download_getter=self.whisper_download_getter,
            verify_model_status=self.verify_model_status,
            cache_model_status=self.cache_model_status,
            resolve_model_dir=self.resolve_model_dir,
        )
        self.runtime_load_service = RuntimeModelLoadService(
            bridge,
            whisper_loader_getter=self.whisper_loader_getter,
            normalize_engine_name=self.normalize_engine_name,
            get_settings_snapshot=self.bridge.get_settings_snapshot,
        )

    @property
    def runtime_model_key(self) -> str:
        return self._runtime_model.key

    @runtime_model_key.setter
    def runtime_model_key(self, value: str) -> None:
        self._runtime_model.key = self.normalize_model_key(str(value))

    @property
    def runtime_model_loaded(self) -> bool:
        return self._runtime_model.loaded

    @runtime_model_loaded.setter
    def runtime_model_loaded(self, value: bool) -> None:
        self._runtime_model.loaded = bool(value)

    @property
    def model_load_running(self) -> bool:
        return self._runtime_model.loading

    @model_load_running.setter
    def model_load_running(self, value: bool) -> None:
        self._runtime_model.loading = bool(value)
        if not self._runtime_model.loading:
            self._runtime_model.started_at = 0.0

    @property
    def runtime_model_message(self) -> str:
        return self._runtime_model.message

    @runtime_model_message.setter
    def runtime_model_message(self, value: str) -> None:
        self._runtime_model.message = str(value)

    def resolve_model_dir(self) -> str:
        configured = self.settings.cache.get("dir_model", "auto")
        return configured if configured != "auto" else get_default_download_root()

    def get_model_manager_keys(self) -> list[str]:
        base_models = ["tiny", "base", "small", "medium", "large-v1", "large-v2", "large-v3"]
        return [model if "large" in model else f"{model}.en" for model in base_models] + base_models

    def estimate_total_whisper_bytes(self, model_key: str) -> int:
        return estimate_whisper_model_bytes(model_key, normalize_model_key=self.normalize_model_key)

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

    def is_runtime_model_loading(self) -> bool:
        return self.model_load_running

    def set_runtime_model_loading(self, loading: bool) -> None:
        self.model_load_running = loading

    def is_runtime_model_ready(self, model_key: str | None = None) -> bool:
        if not self.runtime_model_loaded:
            return False
        if model_key is None:
            return True
        return self.runtime_model_key == self.normalize_model_key(str(model_key))

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

    def build_model_manager_state(self, engine_hint: Optional[str] = None, include_both: bool = False) -> JsonDict:
        self.model_manager_engine = self._normalize_engine_scope(engine_hint)
        models = self.get_model_manager_keys()
        self.model_manager_model = self._normalize_model_scope(self.model_manager_model)
        rows = self._build_model_rows(["whisper", "faster-whisper"] if include_both else [self.model_manager_engine], models)
        selected_model_estimate_bytes = self.estimate_total_whisper_bytes(self.model_manager_model)

        return {
            "engine_options": ["whisper", "faster-whisper"],
            "model_options": models,
            "selected_engine": self.model_manager_engine,
            "selected_model": self.model_manager_model,
            "selected_model_estimate_bytes": selected_model_estimate_bytes,
            "model_dir": self.resolve_model_dir(),
            "download_running": self.model_download_running,
            "view_scope": "both" if include_both else "selected",
            "rows": rows,
        }

    def build_runtime_model_state(self) -> JsonDict:
        return self._runtime_model.build_state()

    def _emit_runtime_model_update(self) -> None:
        self.bridge.emit_ui_update([UI_SECTION_TASK])

    def get_model_manager_state(self, engine: Optional[str] = None) -> JsonDict:
        if engine is not None:
            self.model_manager_engine = str(engine)
        return self.build_model_manager_state(engine)

    def get_runtime_model_state(self) -> JsonDict:
        return self.build_runtime_model_state()

    def mark_runtime_model_pending(self, model_key: str, *, loaded: bool = False, message: Optional[str] = None) -> None:
        self._runtime_model.mark_pending(model_key, loaded=loaded, message=message)
        self._emit_runtime_model_update()

    def mark_runtime_model_ready(self, model_key: Optional[str] = None, *, message: Optional[str] = None) -> None:
        self._runtime_model.mark_ready(model_key, message=message)
        self._emit_runtime_model_update()

    def mark_runtime_model_failed(self, message: str) -> None:
        self._runtime_model.mark_failed(message)
        self._emit_runtime_model_update()

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
        selected_model = self.model_manager_model

        def worker():
            self.model_download_running = True
            try:
                self.download_service.run(ModelDownloadRequest(model_key=selected_model, engine=engine))
            except Exception as exc:
                logger.exception(exc)
                self.cache_model_status(engine, selected_model, False, str(exc), downloading=False)
                self.bridge.update_task_error(str(exc))
            finally:
                self.model_download_running = False

        Thread(target=worker, daemon=True).start()
        return {"ok": True, "message": "Model download started", "model": selected_model, "engine": engine}

    def load_runtime_model(self, model_key: str) -> JsonDict:
        model_key = self._normalize_model_scope(model_key)
        if self.model_load_running:
            return {"ok": False, "message": "Another load is running"}

        self.settings.save_key("model_mw", model_key)
        self.settings.save_key("model_f_import", model_key)
        self.mark_runtime_model_pending(model_key)

        def worker():
            try:
                self.runtime_load_service.run(RuntimeModelLoadRequest(model_key=model_key))
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
        self._runtime_model.handle_task_message(message, source=source)

    def handle_recording_status(self, payload: JsonDict) -> None:
        self._runtime_model.handle_recording_status(payload)
