from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Mapping

from speech_translate.runtime_registry import bridge_state_registry, get_current_bridge, settings_registry
from speech_translate.utils.audio.file_batch_domain import FileBatchStatusContext
from speech_translate.utils.audio.file_runtime_settings import FileRuntimeSettings


@dataclass
class FileUiBridgeAdapter:
    bridge: object | None = None
    bridge_getter: Callable[[], object | None] = get_current_bridge

    def _resolve_bridge(self):
        bridge = self.bridge_getter() if self.bridge is None else self.bridge
        if bridge is None:
            return None
        if hasattr(bridge, "init_file_batch") and hasattr(bridge, "sync_file_status"):
            return bridge
        return getattr(bridge, "import_queue_controller", bridge)

    def init_file_batch(self, task_name: str, files: list[object]) -> None:
        bridge = self._resolve_bridge()
        if bridge is not None:
            bridge.init_file_batch(task_name, files)

    def sync_file_status(self, index: int, status: str, is_completed: bool) -> None:
        bridge = self._resolve_bridge()
        if bridge is not None:
            bridge.sync_file_status(index, status, is_completed)


@dataclass(frozen=True)
class FileSettingsAdapter:
    cache: Mapping[str, object]


@dataclass(frozen=True)
class FileEnvironmentAdapter:
    has_ffmpeg: bool = False


@dataclass(frozen=True)
class FileProcessRequest:
    data_files: list[str]
    model_name_tc: str
    lang_source: str
    lang_target: str
    is_tc: bool
    is_tl: bool
    engine: str


@dataclass(frozen=True)
class FileModRequest:
    data_files: list
    model_name_tc: str
    mode: Literal["refinement", "alignment"]


@dataclass(frozen=True)
class FileTranslateResultRequest:
    data_files: list
    engine: str
    lang_target: str


@dataclass
class FileResultQueueAdapter:
    state: object | None = None
    state_provider: Callable[[], object] | None = None

    def _state(self) -> object:
        if self.state is not None:
            return self.state
        if self.state_provider is None:
            raise RuntimeError("file result queue state is not configured")
        return self.state_provider()

    def get(self):
        return self._state().data_queue.get()

    def put(self, payload) -> None:
        self._state().data_queue.put(payload)


@dataclass
class FileProcessingStateAdapter:
    state: object | None = None
    state_provider: Callable[[], object] | None = None

    def _state(self) -> object | None:
        if self.state is not None:
            return self.state
        return self.state_provider() if self.state_provider is not None else None

    def is_file_processing(self) -> bool:
        state = self._state()
        return bool(state.file_processing) if state is not None else False

    def is_transcribing_file(self) -> bool:
        state = self._state()
        return bool(state.transcribing_file) if state is not None else False

    def is_translating_file(self) -> bool:
        state = self._state()
        return bool(state.translating_file) if state is not None else False

    def reset_file_counts(self) -> None:
        state = self._state()
        if state is None:
            return
        state.file_tced_counter = 0
        state.file_tled_counter = 0

    def increment_transcribed_count(self) -> None:
        state = self._state()
        if state is not None:
            state.file_tced_counter += 1

    def increment_translated_count(self) -> None:
        state = self._state()
        if state is not None:
            state.file_tled_counter += 1

    def transcribed_count(self) -> int:
        state = self._state()
        return int(getattr(state, "file_tced_counter", 0)) if state is not None else 0

    def translated_count(self) -> int:
        state = self._state()
        return int(getattr(state, "file_tled_counter", 0)) if state is not None else 0

    def enable_file_tc(self) -> None:
        state = self._state()
        if state is not None:
            state.transcribing_file = True
            state.file_processing = True

    def enable_file_tl(self) -> None:
        state = self._state()
        if state is not None:
            state.translating_file = True
            state.file_processing = True

    def disable_file_tc(self) -> None:
        state = self._state()
        if state is not None:
            state.transcribing_file = False

    def disable_file_tl(self) -> None:
        state = self._state()
        if state is not None:
            state.translating_file = False

    def disable_file_process(self) -> None:
        state = self._state()
        if state is not None:
            state.file_processing = False
            state.transcribing_file = False
            state.translating_file = False

    def reset_mod_counter(self) -> None:
        state = self._state()
        if state is not None:
            state.mod_file_counter = 0

    def increment_mod_counter(self) -> None:
        state = self._state()
        if state is not None:
            state.mod_file_counter += 1

    def mod_counter(self) -> int:
        state = self._state()
        return int(getattr(state, "mod_file_counter", 0)) if state is not None else 0


def _get_file_settings_store():
    return FileSettingsAdapter(cache=dict(settings_registry.get().cache))


def _get_file_runtime_state():
    return bridge_state_registry.get().file_runtime


def _get_file_recording_runtime_state():
    return bridge_state_registry.get().recording_runtime


def _get_file_visual_runtime_state():
    return bridge_state_registry.get().visual


def build_file_ui_bridge_adapter(
    *,
    bridge: object | None = None,
    bridge_getter: Callable[[], object | None] = get_current_bridge,
) -> FileUiBridgeAdapter:
    return FileUiBridgeAdapter(bridge=bridge, bridge_getter=bridge_getter)


def build_file_result_queue_adapter(
    *,
    state: object | None = None,
    state_provider: Callable[[], object] | None = _get_file_recording_runtime_state,
) -> FileResultQueueAdapter:
    return FileResultQueueAdapter(state=state, state_provider=state_provider)


def build_file_processing_state_adapter(
    *,
    state: object | None = None,
    state_provider: Callable[[], object] | None = _get_file_runtime_state,
) -> FileProcessingStateAdapter:
    return FileProcessingStateAdapter(state=state, state_provider=state_provider)


def build_file_environment_adapter(
    *,
    visual_state: object | None = None,
    visual_state_provider: Callable[[], object] | None = _get_file_visual_runtime_state,
) -> FileEnvironmentAdapter:
    if visual_state is None:
        if visual_state_provider is None:
            return FileEnvironmentAdapter()
        visual_state = visual_state_provider()
    return FileEnvironmentAdapter(has_ffmpeg=bool(getattr(visual_state, "has_ffmpeg", False)))


def _get_file_environment():
    return build_file_environment_adapter()


@dataclass(frozen=True)
class FileProcessDependencies:
    ui_bridge: FileUiBridgeAdapter
    result_queue: FileResultQueueAdapter
    processing_state: FileProcessingStateAdapter
    settings: FileSettingsAdapter
    environment: FileEnvironmentAdapter


@dataclass(frozen=True)
class FileModDependencies:
    ui_bridge: FileUiBridgeAdapter
    result_queue: FileResultQueueAdapter
    processing_state: FileProcessingStateAdapter
    settings: FileSettingsAdapter


@dataclass(frozen=True)
class FileTranslateResultDependencies:
    ui_bridge: FileUiBridgeAdapter
    processing_state: FileProcessingStateAdapter
    settings: FileSettingsAdapter


@dataclass(frozen=True)
class FileProcessRuntime:
    status_context: FileBatchStatusContext
    export_dir: str
    slice_start: int | None
    slice_end: int | None
    tl_engine_whisper: bool
    stable_tc: object
    stable_tl: object
    whisper_args: dict[str, object]
    filters: dict[str, object]
    taskname: str
    started_at: float
    ui_bridge: FileUiBridgeAdapter
    result_queue: FileResultQueueAdapter
    processing_state: FileProcessingStateAdapter
    settings: FileSettingsAdapter
    runtime_settings: FileRuntimeSettings
    environment: FileEnvironmentAdapter


@dataclass(frozen=True)
class FileModRuntime:
    status_context: FileBatchStatusContext
    action: str
    export_dir: str
    slice_start: int | None
    slice_end: int | None
    stable_whisper_api: object
    model: object
    mod_func: Callable
    mod_args: dict[str, object]
    started_at: float
    ui_bridge: FileUiBridgeAdapter
    result_queue: FileResultQueueAdapter
    processing_state: FileProcessingStateAdapter
    settings: FileSettingsAdapter
    runtime_settings: FileRuntimeSettings


@dataclass(frozen=True)
class FileResultTranslateRuntime:
    status_context: FileBatchStatusContext
    export_dir: str
    slice_start: int | None
    slice_end: int | None
    stable_whisper_api: object
    api_kwargs: dict[str, object]
    started_at: float
    ui_bridge: FileUiBridgeAdapter
    processing_state: FileProcessingStateAdapter
    settings: FileSettingsAdapter
    runtime_settings: FileRuntimeSettings

