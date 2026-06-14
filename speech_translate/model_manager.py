from __future__ import annotations

import os
from threading import Thread
from time import sleep, time
from typing import Dict, Optional, cast
from urllib.request import Request, urlopen

from loguru import logger

from speech_translate.controller_protocols import JsonDict, ModelManagerBridge, SettingsStore, WhisperLoadApiGetter
from speech_translate.ui_protocol import TASK_SOURCE_MODEL_DOWNLOAD, TASK_SOURCE_MODEL_LOAD
from speech_translate.utils.whisper.download import (
    get_default_download_root,
    verify_model_faster_whisper,
    verify_model_whisper,
)
from speech_translate.utils.whisper.helper import model_select_dict, model_values
from speech_translate.utils.types import SettingDict


class ModelManagerController:
    """Owns model directory resolution, model status cache, downloads, and runtime model state."""

    def __init__(self, bridge: ModelManagerBridge, settings: SettingsStore, whisper_loader_getter: WhisperLoadApiGetter):
        self.bridge = bridge
        self.settings = settings
        self.whisper_loader_getter = whisper_loader_getter
        self.model_status_cache: Dict[str, JsonDict] = {}
        self.model_download_running = False
        self.model_load_running = False
        self.runtime_model_key = self.normalize_model_key(str(settings.cache.get("model_f_import", "")))
        self.runtime_model_loaded = False
        self.runtime_model_message = "模型未预加载"
        self.model_manager_engine = "whisper"
        self.model_manager_model = "small"

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
                return verify_model_faster_whisper(model_key, model_dir)
            except Exception:
                return False
        return os.path.exists(os.path.join(model_dir, f"{model_key}.pt"))

    def verify_model_status(self, engine: str, model_key: str, model_dir: str) -> tuple[bool, str]:
        try:
            downloaded = (
                verify_model_faster_whisper(model_key, model_dir)
                if engine == "faster-whisper"
                else verify_model_whisper(model_key, model_dir)
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

    @staticmethod
    def path_size(path: str) -> int:
        if not path:
            return 0
        if os.path.isfile(path):
            return os.path.getsize(path)
        if os.path.isdir(path):
            return sum(os.path.getsize(os.path.join(root, file_name)) for root, _, files in os.walk(path) for file_name in files)
        return 0

    @staticmethod
    def format_bytes(value: float) -> str:
        if value <= 0:
            return "0 B"
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if value < 1024.0:
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
            value /= 1024.0
        return f"{value:.1f} PB"

    def estimate_total_whisper_bytes(self, model_key: str) -> int:
        try:
            from whisper import _MODELS

            if url := _MODELS.get(model_key):
                with urlopen(Request(url, method="HEAD"), timeout=6) as resp:
                    return int(resp.headers.get("Content-Length", 0))
        except Exception:
            pass
        return 0

    def build_model_manager_state(self, engine_hint: Optional[str] = None, include_both: bool = False) -> JsonDict:
        self.model_manager_engine = str(engine_hint or self.model_manager_engine or "whisper")
        if self.model_manager_engine not in {"whisper", "faster-whisper"}:
            self.model_manager_engine = "whisper"

        models = self.get_model_manager_keys()
        self.model_manager_model = str(self.model_manager_model or "small")
        if self.model_manager_model not in models:
            self.model_manager_model = "small"

        rows = []
        for row_engine in (["whisper", "faster-whisper"] if include_both else [self.model_manager_engine]):
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
        return {
            "key": self.runtime_model_key,
            "loading": bool(self.model_load_running) and not loaded,
            "loaded": loaded,
            "message": self.runtime_model_message,
        }

    def get_model_manager_state(self, engine: Optional[str] = None) -> JsonDict:
        if engine is not None:
            self.model_manager_engine = str(engine)
        return self.build_model_manager_state(engine)

    def get_runtime_model_state(self) -> JsonDict:
        return self.build_runtime_model_state()

    def mark_runtime_model_pending(self, model_key: str, *, loaded: bool = False, message: Optional[str] = None) -> None:
        normalized_key = self.normalize_model_key(str(model_key))
        self.runtime_model_key = normalized_key
        self.runtime_model_loaded = bool(loaded)
        self.model_load_running = not bool(loaded)
        self.runtime_model_message = message or (
            f"Model ready: {normalized_key}" if loaded else f"Loading model cache for {normalized_key}"
        )

    def mark_runtime_model_ready(self, model_key: Optional[str] = None, *, message: Optional[str] = None) -> None:
        normalized_key = self.normalize_model_key(str(model_key or self.runtime_model_key))
        self.runtime_model_key = normalized_key
        self.runtime_model_loaded = True
        self.model_load_running = False
        self.runtime_model_message = message or f"Model ready: {normalized_key}"

    def mark_runtime_model_failed(self, message: str) -> None:
        self.model_load_running = False
        self.runtime_model_loaded = False
        self.runtime_model_message = str(message)

    def check_model(self, model_key: str, engine: str = "whisper") -> JsonDict:
        engine = engine.strip().lower()
        self.model_manager_engine = engine if engine in {"whisper", "faster-whisper"} else "whisper"
        self.model_manager_model = model_key

        downloaded, error = self.verify_model_status(self.model_manager_engine, model_key, self.resolve_model_dir())
        self.cache_model_status(self.model_manager_engine, model_key, downloaded, error, downloading=False)
        state = self.build_model_manager_state(self.model_manager_engine)
        state["checked"] = {
            "model": model_key,
            "engine": self.model_manager_engine,
            "downloaded": downloaded,
            "error": error,
        }
        return state

    def check_all_models(self, engine: str = "whisper") -> JsonDict:
        engine = engine.strip().lower()
        if engine not in {"whisper", "faster-whisper", "both"}:
            engine = "whisper"
        if engine != "both":
            self.model_manager_engine = engine

        model_dir = self.resolve_model_dir()
        for target_engine in (["whisper", "faster-whisper"] if engine == "both" else [engine]):
            for model_key in self.get_model_manager_keys():
                downloaded, error = self.verify_model_status(target_engine, model_key, model_dir)
                self.cache_model_status(target_engine, model_key, downloaded, error, downloading=False)

        return self.build_model_manager_state(self.model_manager_engine, include_both=(engine == "both"))

    def download_model(self, model_key: str, engine: str = "whisper") -> JsonDict:
        engine = engine.strip().lower()
        engine = engine if engine in {"whisper", "faster-whisper"} else "whisper"
        if self.model_download_running:
            return {"ok": False, "message": "Another download is running"}

        self.model_manager_engine = engine
        self.model_manager_model = model_key

        def worker():
            self.model_download_running = True
            try:
                model_dir = self.resolve_model_dir()
                os.makedirs(model_dir, exist_ok=True)
                self.bridge.reset_task_state("Model Download")
                self.bridge.update_task_message(f"Preparing download for {model_key} ({engine})", source=TASK_SOURCE_MODEL_DOWNLOAD)
                self.bridge.update_task_progress(5, source=TASK_SOURCE_MODEL_DOWNLOAD)

                if engine == "whisper":
                    from whisper import _MODELS

                    if not (url := _MODELS.get(model_key)):
                        raise ValueError(f"Invalid model key: {model_key}")
                    observe_path = os.path.join(model_dir, os.path.basename(url))
                    total_bytes = self.estimate_total_whisper_bytes(model_key)
                else:
                    from faster_whisper.utils import _MODELS as fw_models
                    from huggingface_hub.file_download import repo_folder_name

                    if not (repo_id := fw_models.get(model_key)):
                        raise ValueError(f"Invalid model key: {model_key}")
                    observe_path = os.path.join(model_dir, repo_folder_name(repo_id=repo_id, repo_type="model"))
                    try:
                        self.bridge.update_task_message(f"Fetching model info for {model_key}...", source=TASK_SOURCE_MODEL_DOWNLOAD)
                        import huggingface_hub

                        api = huggingface_hub.HfApi()
                        repo_info = api.repo_info(repo_id=repo_id, repo_type="model", files_metadata=True)
                        allow_patterns = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
                        filtered = list(
                            huggingface_hub.utils.filter_repo_objects(
                                items=[file_info.rfilename for file_info in repo_info.siblings],
                                allow_patterns=allow_patterns,
                            )
                        )
                        total_bytes = sum(
                            file_info.size
                            for file_info in repo_info.siblings
                            if file_info.rfilename in filtered and file_info.size is not None
                        )
                    except Exception as exc:
                        logger.warning(f"Failed to fetch total size: {exc}")
                        total_bytes = 0

                self.cache_model_status(engine, model_key, False, downloading=True, progress=5, speed="-")
                result_box = {"ok": False, "error": None}

                def _do_download():
                    try:
                        if engine == "whisper":
                            from whisper import _MODELS, _download

                            _download(_MODELS.get(model_key), model_dir, False)
                        else:
                            from faster_whisper.utils import download_model as fw_download_model

                            fw_download_model(model_key, cache_dir=model_dir)
                        result_box["ok"] = True
                    except Exception as exc:
                        result_box["error"] = exc

                download_thread = Thread(target=_do_download, daemon=True)
                download_thread.start()

                last_bytes, last_time, start_t = 0, time(), time()
                while download_thread.is_alive():
                    sleep(0.6)
                    current_bytes, now = self.path_size(observe_path), time()
                    speed_bps = max(0, current_bytes - last_bytes) / max(0.2, now - last_time)
                    speed_text = f"{self.format_bytes(speed_bps)}/s" if speed_bps > 0 else "-"
                    progress = min(
                        95.0,
                        max(5.0, (current_bytes / total_bytes * 95.0) if total_bytes > 0 else (5.0 + (now - start_t) * 0.9)),
                    )
                    size_text = (
                        f"{self.format_bytes(current_bytes)}/{self.format_bytes(total_bytes)}"
                        if total_bytes > 0
                        else self.format_bytes(current_bytes)
                    )

                    self.cache_model_status(engine, model_key, False, downloading=True, progress=progress, speed=speed_text)
                    self.bridge.update_task_progress(progress, source=TASK_SOURCE_MODEL_DOWNLOAD)
                    self.bridge.update_task_message(f"DL {model_key}: {size_text} ({speed_text})", source=TASK_SOURCE_MODEL_DOWNLOAD)
                    last_bytes, last_time = current_bytes, now

                download_thread.join()
                if result_box.get("error"):
                    raise cast(Exception, result_box["error"])

                self.bridge.update_task_progress(90, source=TASK_SOURCE_MODEL_DOWNLOAD)
                downloaded = False
                error = ""
                for _ in range(8):
                    if downloaded := self.verify_model_status(engine, model_key, model_dir)[0]:
                        break
                    sleep(0.5)

                self.cache_model_status(
                    engine,
                    model_key,
                    downloaded,
                    error,
                    downloading=False,
                    progress=100.0 if downloaded else 0.0,
                    speed="-",
                )
                if not downloaded:
                    raise RuntimeError(error or "Verification failed")

                self.bridge.update_task_progress(100, source=TASK_SOURCE_MODEL_DOWNLOAD)
                self.bridge.finish_task(f"Model downloaded: {model_key} ({engine})")
            except Exception as exc:
                logger.exception(exc)
                self.cache_model_status(engine, model_key, False, str(exc), downloading=False)
                self.bridge.update_task_error(str(exc))
            finally:
                self.model_download_running = False

        Thread(target=worker, daemon=True).start()
        return {"ok": True, "message": "Model download started", "model": model_key, "engine": engine}

    def load_runtime_model(self, model_key: str) -> JsonDict:
        model_key = self.normalize_model_key(str(model_key))
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

                self.bridge.reset_task_state("Model Load")
                self.bridge.update_task_message(f"Loading model cache for {model_key}", source=TASK_SOURCE_MODEL_LOAD)
                self.bridge.update_task_progress(5)

                whisper_load_api.get_model(
                    bool(settings_snapshot.get("transcribe_mw", True)),
                    bool(settings_snapshot.get("translate_mw", True)),
                    engine in model_values,
                    model_key,
                    engine,
                    settings_snapshot,
                    **whisper_load_api.get_model_args(settings_snapshot),
                )

                self.bridge.update_task_progress(100)
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
