from __future__ import annotations

from dataclasses import dataclass

from speech_translate.bridge_runtime_state import BridgeFileRuntime, BridgeRecordingRuntime, BridgeVisualRuntime
from speech_translate.controller_protocols import JsonDict
from speech_translate.utils.whisper.helper import model_keys


@dataclass(frozen=True)
class ImportStartContext:
    settings_snapshot: JsonDict
    engine: str
    model_name_tc: str
    is_tc: bool
    is_tl: bool
    files_to_process: list[str]

    @property
    def should_prepare_runtime_model(self) -> bool:
        return self.is_tc or (self.is_tl and self.engine in model_keys)

    @property
    def should_auto_close_selenium(self) -> bool:
        return self.is_tl and self.engine == "Selenium Chrome Translate"


@dataclass
class ImportQueueProcessRuntime:
    recording_state: BridgeRecordingRuntime
    file_state: BridgeFileRuntime

    def is_recording_active(self) -> bool:
        return bool(getattr(self.recording_state, "recording", False))

    def is_file_processing_active(self) -> bool:
        return bool(getattr(self.file_state, "file_processing", False))

    def enable_file_processing(self) -> None:
        self.file_state.file_processing = True

    def disable_file_processing(self) -> None:
        self.file_state.file_processing = False

    def transcribed_count(self) -> int:
        return int(getattr(self.file_state, "file_tced_counter", 0))

    def translated_count(self) -> int:
        return int(getattr(self.file_state, "file_tled_counter", 0))


@dataclass(frozen=True)
class ImportQueueRuntimeBindings:
    recording_state: BridgeRecordingRuntime
    file_state: BridgeFileRuntime
    visual_state: BridgeVisualRuntime

    def build_process_runtime(self) -> ImportQueueProcessRuntime:
        return ImportQueueProcessRuntime(
            recording_state=self.recording_state,
            file_state=self.file_state,
        )


def build_import_start_context(
    settings_snapshot: JsonDict,
    *,
    normalize_engine_name,
    normalize_model_key,
    files_to_process: list[str],
) -> ImportStartContext:
    return ImportStartContext(
        settings_snapshot=settings_snapshot,
        engine=normalize_engine_name(str(settings_snapshot.get("tl_engine_f_import", "Google Translate"))),
        model_name_tc=normalize_model_key(str(settings_snapshot.get("model_f_import", ""))),
        is_tc=bool(settings_snapshot.get("transcribe_f_import", True)),
        is_tl=bool(settings_snapshot.get("translate_f_import", True)),
        files_to_process=list(files_to_process),
    )


__all__ = [
    "ImportQueueProcessRuntime",
    "ImportQueueRuntimeBindings",
    "ImportStartContext",
    "build_import_start_context",
]
