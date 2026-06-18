from __future__ import annotations

import os
from dataclasses import dataclass
from time import sleep
from typing import Callable

from speech_translate.controller_protocols import JsonDict, ModelManagerBridge, WhisperLoadApiGetter
from speech_translate.controller_settings import build_runtime_model_load_settings
from speech_translate.ui_protocol import TASK_SOURCE_MODEL_DOWNLOAD, TASK_SOURCE_MODEL_LOAD
from speech_translate.utils.whisper.download_runtime import TaskReporter


VerifyModelStatusFn = Callable[[str, str, str], tuple[bool, str]]
CacheModelStatusFn = Callable[..., None]
ResolveModelDirFn = Callable[[], str]
NormalizeEngineNameFn = Callable[[str], str]
WhisperDownloadApiGetter = Callable[[], object]


@dataclass(frozen=True)
class ModelDownloadRequest:
    model_key: str
    engine: str


@dataclass(frozen=True)
class RuntimeModelLoadRequest:
    model_key: str


class ModelDownloadService:
    """Execute model download orchestration independently from controller state ownership."""

    def __init__(
        self,
        bridge: ModelManagerBridge,
        *,
        whisper_download_getter: WhisperDownloadApiGetter,
        verify_model_status: VerifyModelStatusFn,
        cache_model_status: CacheModelStatusFn,
        resolve_model_dir: ResolveModelDirFn,
    ) -> None:
        self.bridge = bridge
        self.whisper_download_getter = whisper_download_getter
        self.verify_model_status = verify_model_status
        self.cache_model_status = cache_model_status
        self.resolve_model_dir = resolve_model_dir

    def _build_download_reporter(self) -> TaskReporter:
        return TaskReporter(
            reset_task_state=lambda _title: None,
            update_task_message=lambda message: self.bridge.update_task_message(message, source=TASK_SOURCE_MODEL_DOWNLOAD),
            update_task_progress=lambda value: self.bridge.update_task_progress(value, source=TASK_SOURCE_MODEL_DOWNLOAD),
            finish_task=lambda _message: None,
            update_task_error=lambda _message: None,
        )

    def run(self, request: ModelDownloadRequest) -> None:
        whisper_download_api = self.whisper_download_getter()
        model_dir = self.resolve_model_dir()
        os.makedirs(model_dir, exist_ok=True)

        self.bridge.reset_task_state("Model Download")
        self.bridge.update_task_message(
            f"Preparing download for {request.model_key} ({request.engine})",
            source=TASK_SOURCE_MODEL_DOWNLOAD,
        )
        self.bridge.update_task_progress(5, source=TASK_SOURCE_MODEL_DOWNLOAD)
        self.cache_model_status(request.engine, request.model_key, False, downloading=True, progress=5, speed="-")

        success = whisper_download_api.download_model(
            request.model_key,
            use_faster_whisper=(request.engine == "faster-whisper"),
            download_root=model_dir,
            reporter=self._build_download_reporter(),
            progress_floor=5.0,
            progress_ceiling=90.0,
            progress_callback=lambda snapshot: self.cache_model_status(
                request.engine,
                request.model_key,
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
            downloaded, error = self.verify_model_status(request.engine, request.model_key, model_dir)
            if downloaded:
                break
            sleep(0.5)

        self.cache_model_status(
            request.engine,
            request.model_key,
            downloaded,
            error,
            downloading=False,
            progress=100.0 if downloaded else 0.0,
            speed="-",
        )
        if not downloaded:
            raise RuntimeError(error or "Verification failed")

        self.bridge.update_task_progress(100, source=TASK_SOURCE_MODEL_DOWNLOAD)
        self.bridge.finish_task(f"Model downloaded: {request.model_key} ({request.engine})")


class RuntimeModelLoadService:
    """Execute runtime model-load orchestration independently from controller state ownership."""

    def __init__(
        self,
        bridge: ModelManagerBridge,
        *,
        whisper_loader_getter: WhisperLoadApiGetter,
        normalize_engine_name: NormalizeEngineNameFn,
        get_settings_snapshot: Callable[[], JsonDict],
    ) -> None:
        self.bridge = bridge
        self.whisper_loader_getter = whisper_loader_getter
        self.normalize_engine_name = normalize_engine_name
        self.get_settings_snapshot = get_settings_snapshot

    def run(self, request: RuntimeModelLoadRequest) -> None:
        whisper_load_api = self.whisper_loader_getter()
        load_settings = build_runtime_model_load_settings(
            self.get_settings_snapshot(),
            model_key=request.model_key,
            normalize_engine_name=self.normalize_engine_name,
        )

        self.bridge.reset_task_state("Model Load")
        self.bridge.update_task_message(
            f"Loading model cache for {request.model_key}",
            source=TASK_SOURCE_MODEL_LOAD,
        )
        self.bridge.update_task_progress(5, source=TASK_SOURCE_MODEL_LOAD)
        self.bridge.update_task_message(
            f"Preparing model arguments for {request.model_key}",
            source=TASK_SOURCE_MODEL_LOAD,
        )

        model_args = whisper_load_api.get_model_args(load_settings.snapshot)
        self.bridge.update_task_progress(15, source=TASK_SOURCE_MODEL_LOAD)
        self.bridge.update_task_message(
            f"Checking model cache for {request.model_key}",
            source=TASK_SOURCE_MODEL_LOAD,
        )

        bundle_cached = whisper_load_api.is_model_bundle_cached(
            load_settings.transcribe_enabled,
            load_settings.translate_enabled,
            load_settings.tl_engine_whisper,
            load_settings.model_key,
            load_settings.engine,
            load_settings.snapshot,
            **model_args,
        )
        if bundle_cached:
            self.bridge.update_task_progress(80, source=TASK_SOURCE_MODEL_LOAD)
            self.bridge.update_task_message(
                f"Using cached runtime bundle for {request.model_key}",
                source=TASK_SOURCE_MODEL_LOAD,
            )
        else:
            self.bridge.update_task_progress(35, source=TASK_SOURCE_MODEL_LOAD)
            self.bridge.update_task_message(
                f"Loading model into runtime memory for {request.model_key}",
                source=TASK_SOURCE_MODEL_LOAD,
            )

        whisper_load_api.get_model(
            load_settings.transcribe_enabled,
            load_settings.translate_enabled,
            load_settings.tl_engine_whisper,
            load_settings.model_key,
            load_settings.engine,
            load_settings.snapshot,
            **model_args,
        )

        self.bridge.update_task_progress(100, source=TASK_SOURCE_MODEL_LOAD)
        self.bridge.finish_task(f"Model ready: {request.model_key}")


__all__ = [
    "ModelDownloadRequest",
    "ModelDownloadService",
    "RuntimeModelLoadRequest",
    "RuntimeModelLoadService",
]
