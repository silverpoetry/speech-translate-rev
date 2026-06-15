import json
from dataclasses import dataclass, field
from datetime import datetime
from os import makedirs, path
from threading import Thread
from time import gmtime, sleep, strftime, time
from typing import Callable, Dict, List, Literal, Mapping
import os

from speech_translate._logging import logger
from speech_translate._path import dir_alignment, dir_export, dir_refinement, dir_translate
from speech_translate.runtime_registry import bridge_state_registry, settings_registry
from speech_translate.runtime_deps import empty_torch_cuda_cache, get_stable_whisper, get_whisper_to_language_code
from speech_translate.web_bridge_runtime import WebBridgeRegistry, web_bridge_registry
from speech_translate.utils.translate.language import get_whisper_lang_name, get_whisper_lang_similar

from ..helper import filename_only, get_proxies, kill_thread, start_file
from ..translate.translator import translate
from ..whisper.helper import get_hallucination_filter, get_task_format, model_values, to_language_name
from ..whisper.result import remove_segments_by_str, split_res
from ..whisper.save import save_output_stable_ts

# =========================================================================
# GLOBAL STATE & DECOUPLED UI SYNC
# =========================================================================

ACTIVE_STATUSES = {"Waiting", "Transcribing please wait...", "Translating please wait...", "Processing", "Re-transcribing..."}
StageKey = Literal["tc", "tl", "mod"]
StatusMap = Dict[int, str]


@dataclass
class FileUiBridgeAdapter:
    bridge: object | None = None
    bridge_registry: WebBridgeRegistry = field(default_factory=lambda: web_bridge_registry)

    def _resolve_bridge(self):
        return self.bridge_registry.get() if self.bridge is None else self.bridge

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


@dataclass
class FileResultQueueAdapter:
    state: object | None = None

    def get(self):
        if self.state is None:
            raise RuntimeError("file result queue state is not configured")
        return self.state.data_queue.get()

    def put(self, payload) -> None:
        if self.state is None:
            raise RuntimeError("file result queue state is not configured")
        self.state.data_queue.put(payload)


@dataclass
class FileProcessingStateAdapter:
    state: object | None = None

    def is_file_processing(self) -> bool:
        return bool(self.state.file_processing) if self.state is not None else False

    def is_transcribing_file(self) -> bool:
        return bool(self.state.transcribing_file) if self.state is not None else False

    def is_translating_file(self) -> bool:
        return bool(self.state.translating_file) if self.state is not None else False

    def reset_file_counts(self) -> None:
        if self.state is None:
            return
        self.state.file_tced_counter = 0
        self.state.file_tled_counter = 0

    def increment_transcribed_count(self) -> None:
        if self.state is not None:
            self.state.file_tced_counter += 1

    def increment_translated_count(self) -> None:
        if self.state is not None:
            self.state.file_tled_counter += 1

    def transcribed_count(self) -> int:
        return int(getattr(self.state, "file_tced_counter", 0)) if self.state is not None else 0

    def translated_count(self) -> int:
        return int(getattr(self.state, "file_tled_counter", 0)) if self.state is not None else 0

    def enable_file_tc(self) -> None:
        if self.state is not None:
            self.state.enable_file_tc()

    def enable_file_tl(self) -> None:
        if self.state is not None:
            self.state.enable_file_tl()

    def disable_file_tc(self) -> None:
        if self.state is not None:
            self.state.disable_file_tc()

    def disable_file_tl(self) -> None:
        if self.state is not None:
            self.state.disable_file_tl()

    def disable_file_process(self) -> None:
        if self.state is not None:
            self.state.disable_file_process()

    def reset_mod_counter(self) -> None:
        if self.state is not None:
            self.state.mod_file_counter = 0

    def increment_mod_counter(self) -> None:
        if self.state is not None:
            self.state.mod_file_counter += 1

    def mod_counter(self) -> int:
        return int(getattr(self.state, "mod_file_counter", 0)) if self.state is not None else 0


def _get_file_settings_store():
    return FileSettingsAdapter(cache=settings_registry.get().cache)


def _get_file_runtime_state():
    return bridge_state_registry.get().file_runtime


def _get_file_recording_runtime_state():
    return bridge_state_registry.get().recording_runtime


def _get_file_visual_runtime_state():
    return bridge_state_registry.get().visual


def _get_file_environment():
    return FileEnvironmentAdapter(has_ffmpeg=bool(getattr(_get_file_visual_runtime_state(), "has_ffmpeg", False)))


file_ui_bridge = FileUiBridgeAdapter()
file_result_queue = FileResultQueueAdapter(state=_get_file_recording_runtime_state())
file_processing_state = FileProcessingStateAdapter(state=_get_file_runtime_state())


def _get_whisper_runtime_api():
    from speech_translate.utils.whisper import load as whisper_load_api

    return whisper_load_api


def get_model(*args, **kwargs):
    return _get_whisper_runtime_api().get_model(*args, **kwargs)


def get_model_args(*args, **kwargs):
    return _get_whisper_runtime_api().get_model_args(*args, **kwargs)


def get_tc_args(*args, **kwargs):
    return _get_whisper_runtime_api().get_tc_args(*args, **kwargs)


@dataclass
class WorkerFailure:
    failed: bool = False
    error: Exception | None = None

    def capture(self, exc: Exception) -> None:
        self.failed = True
        self.error = exc

    def raise_if_failed(self) -> None:
        if self.failed:
            raise self.error or RuntimeError("Unknown worker failure")


@dataclass
class FileBatchStatusContext:
    is_tc: bool = False
    is_tl: bool = False
    is_mod: bool = False
    ui_bridge: FileUiBridgeAdapter | None = None
    tc_status: StatusMap | None = None
    tl_status: StatusMap | None = None
    mod_status: StatusMap | None = None

    def __post_init__(self) -> None:
        if self.tc_status is None:
            self.tc_status = {}
        if self.tl_status is None:
            self.tl_status = {}
        if self.mod_status is None:
            self.mod_status = {}

    def status_map(self, stage: StageKey) -> StatusMap:
        if stage == "tc":
            return self.tc_status
        if stage == "tl":
            return self.tl_status
        return self.mod_status

    def combined_status(self, index: int) -> str:
        return _build_combined_status(
            index,
            is_tc=self.is_tc,
            is_tl=self.is_tl,
            is_mod=self.is_mod,
            tc_status=self.tc_status,
            tl_status=self.tl_status,
            mod_status=self.mod_status,
        )

    def is_completed(self, index: int, combined_status: str | None = None) -> bool:
        combined_status = self.combined_status(index) if combined_status is None else combined_status
        return _is_file_status_completed(
            index,
            combined_status,
            is_tc=self.is_tc,
            is_tl=self.is_tl,
            is_mod=self.is_mod,
            tc_status=self.tc_status,
            tl_status=self.tl_status,
            mod_status=self.mod_status,
        )

    def is_active(self, index: int) -> bool:
        return any(
            enabled and self.status_map(stage).get(index, "Waiting") in ACTIVE_STATUSES
            for stage, enabled in (("tc", self.is_tc), ("tl", self.is_tl), ("mod", self.is_mod))
        )

    def has_active_work(self, item_count: int) -> bool:
        return any(self.is_active(index) for index in range(item_count))

    def sync_ui(self, index: int) -> None:
        combined_status = self.combined_status(index)
        bridge_adapter = self.ui_bridge or file_ui_bridge
        bridge_adapter.sync_file_status(index, combined_status, self.is_completed(index, combined_status))

    def update_status(self, stage: StageKey, index: int, msg: str) -> None:
        self.status_map(stage)[index] = msg
        try:
            self.sync_ui(index)
        except Exception as exc:
            logger.error(f"UI Sync Error suppressed: {exc}")


@dataclass(frozen=True)
class FileExportPlan:
    export_dir: str
    base_name: str
    save_name: str
    metadata_path: str

    @property
    def save_base_path(self) -> str:
        return path.join(self.export_dir, self.save_name)


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

def _build_combined_status(
    index: int,
    *,
    is_tc: bool,
    is_tl: bool,
    is_mod: bool,
    tc_status: Mapping[int, str],
    tl_status: Mapping[int, str],
    mod_status: Mapping[int, str],
) -> str:
    parts: list[str] = []
    if is_tc:
        current = tc_status.get(index, "Waiting")
        if current and current != "Waiting":
            parts.append(current)
    if is_tl:
        current = tl_status.get(index, "Waiting")
        if current and current != "Waiting":
            parts.append(current)
    if is_mod:
        current = mod_status.get(index, "Waiting")
        if current and current != "Waiting":
            parts.append(current)
    return ", ".join(parts) if parts else "Waiting"


def _is_file_status_completed(
    index: int,
    combined_status: str,
    *,
    is_tc: bool,
    is_tl: bool,
    is_mod: bool,
    tc_status: Mapping[int, str],
    tl_status: Mapping[int, str],
    mod_status: Mapping[int, str],
) -> bool:
    lower_status = combined_status.lower()
    if "fail" in lower_status or "error" in lower_status or "parse error" in lower_status:
        return True
    if is_tc and is_tl:
        return "transcribed" in tc_status.get(index, "").lower() and "translated" in tl_status.get(index, "").lower()
    if is_tc:
        return "transcribed" in tc_status.get(index, "").lower()
    if is_tl:
        return "translated" in tl_status.get(index, "").lower()
    if is_mod:
        mod_value = mod_status.get(index, "").lower()
        return "refined" in mod_value or "aligned" in mod_value or "translated" in mod_value
    return False

def _update_status(status_context: FileBatchStatusContext, stage: StageKey, index: int, msg: str):
    """修改状态并触发 UI 同步（带防崩溃保护）"""
    status_context.update_status(stage, index, msg)

def _save_metadata(filepath: str, meta_data: dict):
    try:
        makedirs(path.dirname(filepath), exist_ok=True)
        if path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                existing = json.load(f)
                existing.update(meta_data)
                meta_data = existing
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(meta_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.warning(f"Failed to save metadata: {e}")

def _build_base_export_name(template: str, file_name: str, lang_src: str, lang_tgt: str, tc_model: str, tl_engine: str) -> str:
    return (
        template.replace("{file}", file_name)
        .replace("{lang-source}", lang_src)
        .replace("{lang-target}", lang_tgt)
        .replace("{transcribe-with}", tc_model)
        .replace("{translate-with}", tl_engine)
    )


def _build_metadata_name(base_name: str) -> str:
    meta_name = base_name
    for fmt, val in get_task_format("metadata", "metadata", "metadata", "metadata", both=True).items():
        meta_name = meta_name.replace(fmt, val)
    return meta_name


def _apply_task_format(base_name: str, format_dict: Mapping[str, str]) -> str:
    save_name = base_name
    for fmt, val in format_dict.items():
        save_name = save_name.replace(fmt, val)
    return save_name


def _build_export_plan(export_dir: str, base_name: str, format_dict: Mapping[str, str]) -> FileExportPlan:
    save_name = _apply_task_format(base_name, format_dict)
    metadata_name = _build_metadata_name(base_name)
    return FileExportPlan(
        export_dir=export_dir,
        base_name=base_name,
        save_name=save_name,
        metadata_path=path.join(export_dir, metadata_name + ".json"),
    )


def _save_export_plan_metadata(export_plan: FileExportPlan, meta_data: Mapping[str, object]) -> None:
    _save_metadata(export_plan.metadata_path, dict(meta_data))


def _resolve_slice_bounds(setting_cache: Mapping[str, object]) -> tuple[int | None, int | None]:
    slice_start = int(setting_cache["file_slice_start"]) if setting_cache["file_slice_start"] else None
    slice_end = int(setting_cache["file_slice_end"]) if setting_cache["file_slice_end"] else None
    return slice_start, slice_end


def _slice_display_name(file_path: str, *, start: int | None, end: int | None) -> str:
    return filename_only(file_path)[start:end]


def _resolve_process_export_dir(setting_cache: Mapping[str, object]) -> str:
    return dir_export if setting_cache["dir_export"] == "auto" else str(setting_cache["dir_export"])


def _resolve_mod_export_dir(setting_cache: Mapping[str, object], *, action: str) -> str:
    if setting_cache["dir_export"] == "auto":
        return dir_refinement if action == "Refinement" else dir_alignment
    return str(setting_cache["dir_export"]) + f"/@{action.lower()}"


def _resolve_translate_result_export_dir(setting_cache: Mapping[str, object]) -> str:
    if setting_cache["dir_export"] == "auto":
        return dir_translate
    return str(setting_cache["dir_export"]) + "/@translated"


def _build_process_file_runtime(
    *,
    model_name_tc: str,
    lang_source: str,
    engine: str,
    is_tc: bool,
    is_tl: bool,
    setting_cache: Mapping[str, object],
    ui_bridge: FileUiBridgeAdapter | None = None,
    result_queue: FileResultQueueAdapter | None = None,
    processing_state: FileProcessingStateAdapter | None = None,
    settings: FileSettingsAdapter | None = None,
    environment: FileEnvironmentAdapter | None = None,
) -> FileProcessRuntime:
    ui_bridge = ui_bridge or file_ui_bridge
    result_queue = result_queue or file_result_queue
    processing_state = processing_state or file_processing_state
    settings = settings or FileSettingsAdapter(cache=setting_cache)
    environment = environment or _get_file_environment()
    tl_engine_whisper = engine in model_values
    stable_tc = stable_tl = None
    to_args = None
    _, _, stable_tc, stable_tl, to_args = get_model(
        is_tc,
        is_tl,
        tl_engine_whisper,
        model_name_tc,
        engine,
        setting_cache,
        **get_model_args(setting_cache),
    )
    whisper_args = get_tc_args(to_args, setting_cache)
    whisper_args["language"] = (
        get_whisper_to_language_code()[get_whisper_lang_similar(lang_source)]
        if lang_source != "auto detect"
        else None
    )
    whisper_args["verbose"] = None
    taskname = "Transcribe & Translate" if is_tc and is_tl else "Transcribe" if is_tc else "Translate"
    filters = (
        get_hallucination_filter("file", setting_cache["path_filter_file_import"])
        if setting_cache["filter_file_import"]
        else {}
    )
    slice_start, slice_end = _resolve_slice_bounds(setting_cache)
    return FileProcessRuntime(
        status_context=FileBatchStatusContext(is_tc=is_tc, is_tl=is_tl, is_mod=False, ui_bridge=ui_bridge),
        export_dir=_resolve_process_export_dir(setting_cache),
        slice_start=slice_start,
        slice_end=slice_end,
        tl_engine_whisper=tl_engine_whisper,
        stable_tc=stable_tc,
        stable_tl=stable_tl,
        whisper_args=whisper_args,
        filters=filters,
        taskname=taskname,
        started_at=time(),
        ui_bridge=ui_bridge,
        result_queue=result_queue,
        processing_state=processing_state,
        settings=settings,
        environment=environment,
    )


def _build_mod_result_runtime(
    *,
    model_name_tc: str,
    mode: Literal["refinement", "alignment"],
    setting_cache: Mapping[str, object],
    ui_bridge: FileUiBridgeAdapter | None = None,
    result_queue: FileResultQueueAdapter | None = None,
    processing_state: FileProcessingStateAdapter | None = None,
    settings: FileSettingsAdapter | None = None,
) -> FileModRuntime:
    ui_bridge = ui_bridge or file_ui_bridge
    result_queue = result_queue or file_result_queue
    processing_state = processing_state or file_processing_state
    settings = settings or FileSettingsAdapter(cache=setting_cache)
    action = "Refinement" if mode == "refinement" else "Alignment"
    stable_whisper = get_stable_whisper()
    model = stable_whisper.load_model(model_name_tc, **get_model_args(setting_cache))
    mod_func = model.refine if mode == "refinement" else model.align
    slice_start, slice_end = _resolve_slice_bounds(setting_cache)
    return FileModRuntime(
        status_context=FileBatchStatusContext(is_tc=False, is_tl=False, is_mod=True, ui_bridge=ui_bridge),
        action=action,
        export_dir=_resolve_mod_export_dir(setting_cache, action=action),
        slice_start=slice_start,
        slice_end=slice_end,
        stable_whisper_api=stable_whisper,
        model=model,
        mod_func=mod_func,
        mod_args=get_tc_args(mod_func, setting_cache, mode="refine" if mode == "refinement" else "align"),
        started_at=time(),
        ui_bridge=ui_bridge,
        result_queue=result_queue,
        processing_state=processing_state,
        settings=settings,
    )


def _build_translate_result_runtime(
    *,
    engine: str,
    setting_cache: Mapping[str, object],
    ui_bridge: FileUiBridgeAdapter | None = None,
    processing_state: FileProcessingStateAdapter | None = None,
    settings: FileSettingsAdapter | None = None,
) -> FileResultTranslateRuntime:
    ui_bridge = ui_bridge or file_ui_bridge
    processing_state = processing_state or file_processing_state
    settings = settings or FileSettingsAdapter(cache=setting_cache)
    slice_start, slice_end = _resolve_slice_bounds(setting_cache)
    api_kwargs = (
        {"libre_link": setting_cache["libre_link"], "libre_api_key": setting_cache["libre_api_key"]}
        if engine == "LibreTranslate"
        else {}
    )
    return FileResultTranslateRuntime(
        status_context=FileBatchStatusContext(is_tc=False, is_tl=False, is_mod=True, ui_bridge=ui_bridge),
        export_dir=_resolve_translate_result_export_dir(setting_cache),
        slice_start=slice_start,
        slice_end=slice_end,
        stable_whisper_api=get_stable_whisper(),
        api_kwargs=api_kwargs,
        started_at=time(),
        ui_bridge=ui_bridge,
        processing_state=processing_state,
        settings=settings,
    )

def _monitor_thread(thread: Thread, check_cancel: Callable[[], bool]) -> None:
    while thread.is_alive():
        if not check_cancel():
            kill_thread(thread)
            raise Exception("Cancelled")
        sleep(0.1)


def _run_monitored_worker(
    target: Callable,
    *,
    cancel_check: Callable[[], bool],
    args: tuple = (),
    kwargs: Mapping[str, object] | None = None,
) -> None:
    thread = Thread(target=target, args=args, kwargs=dict(kwargs or {}), daemon=True)
    thread.start()
    _monitor_thread(thread, cancel_check)


def _execute_monitored_queue_task(
    target: Callable,
    *,
    cancel_check: Callable[[], bool],
    args: tuple = (),
    kwargs: Mapping[str, object] | None = None,
    fail_status: WorkerFailure | None = None,
    raise_failure: bool = True,
    result_queue: FileResultQueueAdapter | None = None,
):
    result_queue = result_queue or file_result_queue
    _run_monitored_worker(target, cancel_check=cancel_check, args=args, kwargs=kwargs)
    if fail_status is not None:
        if raise_failure:
            fail_status.raise_if_failed()
        elif fail_status.failed:
            return None
    return result_queue.get()

# =========================================================================
# ATOMIC EXECUTORS
# =========================================================================

def run_whisper(
    func,
    audio: str | None,
    task: str,
    fail_status: WorkerFailure,
    *,
    result_queue: FileResultQueueAdapter | None = None,
    environment: FileEnvironmentAdapter | None = None,
    **kwargs,
) -> None:
    result_queue = result_queue or file_result_queue
    environment = environment or _get_file_environment()
    try:
        result = func(audio, task=task, **kwargs)
        result_queue.put(result)
    except Exception as e:
        fail_status.capture(e)
        if "The system cannot find the file specified" in str(e) and not environment.has_ffmpeg:
            fail_status.error = Exception("FFmpeg not found in system path. Please install FFmpeg.")

def run_translate_api(
    query,
    engine: str,
    lang_source: str,
    lang_target: str,
    fail_status: WorkerFailure,
    settings: FileSettingsAdapter,
    **kwargs,
) -> None:
    try:
        segment_texts = [segment.text for segment in query.segments]
        query.language = lang_target
        cache = settings.cache
        _success, result = translate(
            engine,
            segment_texts,
            lang_source,
            lang_target,
            get_proxies(cache["http_proxy"], cache["https_proxy"]),
            cache["debug_translate"],
            **kwargs,
        )

        for segment in query.segments:
            if not result: return
            if isinstance(result, str): raise Exception(result)

            translated_text = " " + str(result.pop(0))
            temp_words = translated_text.split()
            segment_words = [w for w in getattr(segment, "words", []) if hasattr(w, "word")]
            
            if len(temp_words) == len(segment_words):
                for w in segment_words: w.word = " " + temp_words.pop(0)
            elif not segment_words:
                setattr(segment, "_default_text", translated_text)
            else:
                if len(temp_words) > len(segment_words):
                    for idx, word in enumerate(temp_words):
                        target_idx = min(idx, len(segment_words) - 1)
                        if idx < len(segment_words): segment_words[target_idx].word = " " + word
                        else: segment_words[target_idx].word += f" {word}"
                else:
                    last_end = segment_words[-1].end
                    for idx, word in enumerate(temp_words): segment_words[idx].word = " " + word
                    segment.words = segment_words[:len(temp_words)]
                    segment.words[-1].end = last_end
    except Exception as e:
        fail_status.capture(e)

# =========================================================================
# FILE PROCESSORS
# =========================================================================

def _cancellable_tc(
    file_path,
    lang_source,
    lang_target,
    model_name,
    tc_func,
    tl_func,
    auto,
    is_tc,
    is_tl,
    engine,
    export_plan: FileExportPlan,
    index,
    filters,
    *,
    status_context: FileBatchStatusContext,
    processing_state: FileProcessingStateAdapter | None = None,
    result_queue: FileResultQueueAdapter | None = None,
    settings: FileSettingsAdapter,
    environment: FileEnvironmentAdapter,
    **kwargs,
):
    processing_state = processing_state or file_processing_state
    result_queue = result_queue or file_result_queue
    cache = settings.cache
    start = time()
    try:
        _update_status(status_context, "tc", index, "Transcribing please wait...")
        fail_status = WorkerFailure()
        
        format_dict = get_task_format("transcribed", f"transcribed {lang_source}", f"transcribed with {model_name}", f"transcribed {lang_source} with {model_name}")
        format_dict.update(get_task_format("tc", f"tc {lang_source}", f"tc with {model_name}", f"tc {lang_source} with {model_name}", short_only=True))
        tc_export_plan = _build_export_plan(export_plan.export_dir, export_plan.base_name, format_dict)

        result = _execute_monitored_queue_task(
            run_whisper,
            cancel_check=processing_state.is_transcribing_file,
            args=(tc_func, file_path, "transcribe", fail_status),
            kwargs={**kwargs, "result_queue": result_queue, "environment": environment},
            fail_status=fail_status,
            result_queue=result_queue,
        )
        if cache["filter_file_import"]:
            try: result = remove_segments_by_str(result, filters.get(get_whisper_lang_name(result.language) if auto else get_whisper_lang_similar(lang_source), []), cache["filter_file_import_case_sensitive"], cache["filter_file_import_strip"], cache["filter_file_import_ignore_punctuations"], cache["filter_file_import_exact_match"], cache["filter_file_import_similarity"])
            except Exception: pass

        if cache["remove_repetition_file_import"]: result = result.remove_repetition(cache["remove_repetition_amount"])

        if is_tc:
            if result.text.strip():
                processing_state.increment_transcribed_count()
                stable_whisper = get_stable_whisper()
                save_output_stable_ts(
                    split_res(stable_whisper.WhisperResult(result.to_dict()), cache),
                    tc_export_plan.save_base_path,
                    cache["export_to"],
                    settings,
                    source_media_path=file_path,
                )
            else:
                _update_status(status_context, "tc", index, "TC Fail! Got empty text")

        _update_status(status_context, "tc", index, "Transcribed")
        _save_export_plan_metadata(export_plan, {"transcribe_time": time() - start, "transcribe_success": True})

        if is_tl:
            tl_query = file_path if engine in model_values else result
            Thread(
                target=_cancellable_tl,
                args=[tl_query, lang_source, lang_target, tl_func, engine, export_plan, index, file_path, filters],
                kwargs={**kwargs, "status_context": status_context, "processing_state": processing_state, "result_queue": result_queue, "settings": settings, "environment": environment},
                daemon=True,
            ).start()
            
    except Exception as e:
        _update_status(status_context, "tc", index, "Failed to transcribe")
        if is_tl:
            _update_status(status_context, "tl", index, "Skipped (TC Failed)")
        if str(e) != "Cancelled": logger.error(f"TC Error: {e}")

def _cancellable_tl(
    query,
    lang_source,
    lang_target,
    tl_func,
    engine,
    export_plan: FileExportPlan,
    index,
    media_path,
    filters,
    *,
    status_context: FileBatchStatusContext,
    processing_state: FileProcessingStateAdapter | None = None,
    result_queue: FileResultQueueAdapter | None = None,
    settings: FileSettingsAdapter,
    environment: FileEnvironmentAdapter,
    **kwargs,
):
    processing_state = processing_state or file_processing_state
    result_queue = result_queue or file_result_queue
    cache = settings.cache
    start = time()
    try:
        _update_status(status_context, "tl", index, "Translating please wait...")
        fail_status = WorkerFailure()

        format_dict = get_task_format("translated", f"translated {lang_source} to {lang_target}", f"translated with {engine}", f"translated {lang_source} to {lang_target} with {engine}")
        format_dict.update(get_task_format("tl", f"tl {lang_source} to {lang_target}", f"tl with {engine}", f"tl {lang_source} to {lang_target} with {engine}", short_only=True))
        tl_export_plan = _build_export_plan(export_plan.export_dir, export_plan.base_name, format_dict)

        if engine in model_values:
            result = _execute_monitored_queue_task(
                run_whisper,
                cancel_check=processing_state.is_translating_file,
                args=(tl_func, query, "translate", fail_status),
                kwargs={**kwargs, "result_queue": result_queue, "environment": environment},
                fail_status=fail_status,
                result_queue=result_queue,
            )
            if cache["filter_file_import"]:
                try: result = remove_segments_by_str(result, filters.get("english", []), cache["filter_file_import_case_sensitive"], cache["filter_file_import_strip"], cache["filter_file_import_ignore_punctuations"], cache["filter_file_import_exact_match"], cache["filter_file_import_similarity"])
                except Exception: pass
            if cache["remove_repetition_file_import"]: result = result.remove_repetition(cache["remove_repetition_amount"])
        else:
            if not getattr(query, "text", "").strip():
                return _update_status(status_context, "tl", index, "TL Fail! Empty text")
            api_kwargs = {"libre_link": cache["libre_link"], "libre_api_key": cache["libre_api_key"]} if engine == "LibreTranslate" else {}
            _run_monitored_worker(
                run_translate_api,
                cancel_check=processing_state.is_translating_file,
                args=(query, engine, lang_source, lang_target, fail_status, settings),
                kwargs=api_kwargs,
            )
            fail_status.raise_if_failed()
            result = query

        if not getattr(result, "text", "").strip():
            return _update_status(status_context, "tl", index, "TL Fail! Empty text")

        processing_state.increment_translated_count()
        save_output_stable_ts(split_res(result, cache), tl_export_plan.save_base_path, cache["export_to"], settings, source_media_path=media_path)
        _update_status(status_context, "tl", index, "Translated")
        _save_export_plan_metadata(export_plan, {"translate_time": time() - start, "translate_success": True})

    except Exception as e:
        _update_status(status_context, "tl", index, "Failed to translate")
        if str(e) != "Cancelled": logger.error(f"TL Error: {e}")

# =========================================================================
# PUBLIC BATCH APIS
# =========================================================================

def process_file(
    data_files: List[str],
    model_name_tc: str,
    lang_source: str,
    lang_target: str,
    is_tc: bool,
    is_tl: bool,
    engine: str,
    *,
    ui_bridge: FileUiBridgeAdapter | None = None,
    result_queue: FileResultQueueAdapter | None = None,
    processing_state: FileProcessingStateAdapter | None = None,
    settings: FileSettingsAdapter | None = None,
    environment: FileEnvironmentAdapter | None = None,
    open_dir_fn: Callable[[str], None] = start_file,
) -> None:
    try:
        ui_bridge = ui_bridge or file_ui_bridge
        result_queue = result_queue or file_result_queue
        processing_state = processing_state or file_processing_state
        settings = settings or _get_file_settings_store()
        environment = environment or _get_file_environment()
        runtime = _build_process_file_runtime(
            model_name_tc=model_name_tc,
            lang_source=lang_source,
            engine=engine,
            is_tc=is_tc,
            is_tl=is_tl,
            setting_cache=settings.cache,
            ui_bridge=ui_bridge,
            result_queue=result_queue,
            processing_state=processing_state,
            settings=settings,
            environment=environment,
        )
        status_context = runtime.status_context
        processing_state.reset_file_counts()

        processing_state.enable_file_tc()
        processing_state.enable_file_tl()

        runtime.ui_bridge.init_file_batch(f"Task: {runtime.taskname} with {model_name_tc}", data_files)

        def is_still_active():
            return status_context.has_active_work(len(data_files))

        for i, file in enumerate(data_files):
            if not processing_state.is_file_processing():
                break
            logger.info(f"Loop entered for file: {file}")
            file_name = _slice_display_name(file, start=runtime.slice_start, end=runtime.slice_end)
            base_name = _build_base_export_name(
                datetime.now().strftime(runtime.settings.cache["export_format"]),
                file_name,
                lang_source,
                lang_target,
                model_name_tc,
                engine,
            )
            export_plan = _build_export_plan(runtime.export_dir, base_name, {})

            _save_export_plan_metadata(export_plan, {
                "meta_written_at": str(datetime.now()), "task": runtime.taskname, "filename": file_name,
                "transcribe": is_tc, "translate": is_tl, "model": model_name_tc, "engine": engine
            })

            if is_tl and not is_tc and runtime.tl_engine_whisper:
                Thread(
                    target=_cancellable_tl,
                    args=[file, lang_source, lang_target, runtime.stable_tl, engine, export_plan, i, file, runtime.filters],
                    kwargs={**runtime.whisper_args, "status_context": status_context, "processing_state": processing_state, "result_queue": result_queue, "settings": runtime.settings, "environment": runtime.environment},
                    daemon=True,
                ).start()
            else:
                tc_thread = Thread(
                    target=_cancellable_tc,
                    args=[file, lang_source, lang_target, model_name_tc, runtime.stable_tc, runtime.stable_tl, lang_source == "auto detect", is_tc, is_tl, engine, export_plan, i, runtime.filters],
                    kwargs={**runtime.whisper_args, "status_context": status_context, "processing_state": processing_state, "result_queue": result_queue, "settings": runtime.settings, "environment": runtime.environment},
                    daemon=True,
                )
                tc_thread.start()
                tc_thread.join()

        while processing_state.is_file_processing() and is_still_active():
            sleep(0.5)

        logger.info(f"Process FILE completed in {time() - runtime.started_at:.2f}s")
        if (processing_state.transcribed_count() > 0 or processing_state.translated_count() > 0) and runtime.settings.cache["auto_open_dir_export"]:
            open_dir_fn(runtime.export_dir)

    except Exception as e:
        logger.error(f"Process FILE error: {e}")
    finally:
        processing_state.disable_file_process()
        processing_state.disable_file_tc()
        processing_state.disable_file_tl()
        empty_torch_cuda_cache()


def mod_result(
    data_files: List,
    model_name_tc: str,
    mode: Literal["refinement", "alignment"],
    *,
    ui_bridge: FileUiBridgeAdapter | None = None,
    result_queue: FileResultQueueAdapter | None = None,
    processing_state: FileProcessingStateAdapter | None = None,
    settings: FileSettingsAdapter | None = None,
    open_dir_fn: Callable[[str], None] = start_file,
):
    try:
        ui_bridge = ui_bridge or file_ui_bridge
        result_queue = result_queue or file_result_queue
        processing_state = processing_state or file_processing_state
        settings = settings or _get_file_settings_store()
        runtime = _build_mod_result_runtime(
            model_name_tc=model_name_tc,
            mode=mode,
            setting_cache=settings.cache,
            ui_bridge=ui_bridge,
            result_queue=result_queue,
            processing_state=processing_state,
            settings=settings,
        )
        status_context = runtime.status_context
        processing_state.reset_mod_counter()

        runtime.ui_bridge.init_file_batch(f"Task {mode} with {model_name_tc}", [f[0] for f in data_files])

        def is_still_active():
            return status_context.has_active_work(len(data_files))

        for i, file_data in enumerate(data_files):
            if not processing_state.is_file_processing():
                break

            audio_path, mod_path = file_data[0], file_data[1]
            file_name = _slice_display_name(audio_path, start=runtime.slice_start, end=runtime.slice_end)
            base_name = _build_base_export_name(
                datetime.now().strftime(runtime.settings.cache["export_format"]),
                file_name,
                "",
                "",
                model_name_tc,
                "",
            )

            task_short = {"refinement": "rf", "alignment": "al"}
            format_dict = get_task_format(runtime.action, runtime.action, f"{runtime.action} with {model_name_tc}", f"{runtime.action} with {model_name_tc}")
            format_dict.update(get_task_format(task_short[mode], task_short[mode], f"{task_short[mode]} with {model_name_tc}", f"{task_short[mode]} with {model_name_tc}", short_only=True))
            export_plan = _build_export_plan(runtime.export_dir, base_name, format_dict)

            try:
                mod_src = runtime.stable_whisper_api.WhisperResult(mod_path) if mod_path.endswith(".json") else open(mod_path, "r", encoding="utf-8").read()
            except Exception:
                _update_status(status_context, "mod", i, "Parse Error")
                continue

            mod_args = dict(runtime.mod_args)
            if mode == "alignment" and len(file_data) > 2 and len(file_data[2]) > 3:
                mod_args["language"] = get_whisper_to_language_code().get(get_whisper_lang_similar(file_data[2]), "auto")

            def _run_mod():
                try:
                    _update_status(status_context, "mod", i, f"Processing {mode}")
                    res = runtime.mod_func(audio_path, mod_src, **mod_args)
                    runtime.result_queue.put(res)
                except Exception as e:
                    if "'NoneType'" in str(e) and mode == "refinement":
                        try:
                            _update_status(status_context, "mod", i, "Re-transcribing...")
                            res = runtime.model.transcribe(audio_path, **get_tc_args(runtime.model.transcribe, runtime.settings.cache))
                            res = runtime.mod_func(audio_path, res, **mod_args)
                            runtime.result_queue.put(res)
                        except Exception as ee:
                            fail_status.capture(Exception(f"Re-transcribe failed: {ee}"))
                    else:
                        fail_status.capture(e)

            fail_status = WorkerFailure()
            result = _execute_monitored_queue_task(
                _run_mod,
                cancel_check=processing_state.is_file_processing,
                fail_status=fail_status,
                raise_failure=False,
                result_queue=runtime.result_queue,
            )

            if fail_status.failed:
                _update_status(status_context, "mod", i, "Failed")
                continue

            result = split_res(result, runtime.settings.cache)
            if not result.language: result.language = mod_args.get("language", "auto")

            save_output_stable_ts(result, export_plan.save_base_path, runtime.settings.cache["export_to"], runtime.settings)
            processing_state.increment_mod_counter()
            _update_status(status_context, "mod", i, runtime.action)
            _save_export_plan_metadata(export_plan, {"meta_written_at": str(datetime.now()), "task": f"Mod Result ({mode})", "time": time() - runtime.started_at})

        while processing_state.is_file_processing() and is_still_active():
            sleep(0.5)

        logger.info(f"Process MOD completed in {time() - runtime.started_at:.2f}s")
        if processing_state.mod_counter() > 0 and runtime.settings.cache.get(f"auto_open_dir_{mode}", True):
            open_dir_fn(runtime.export_dir)

    except Exception as e:
        logger.error(f"Process MOD error: {e}")
    finally:
        processing_state.disable_file_process()
        empty_torch_cuda_cache()


def translate_result(
    data_files: List,
    engine: str,
    lang_target: str,
    *,
    ui_bridge: FileUiBridgeAdapter | None = None,
    processing_state: FileProcessingStateAdapter | None = None,
    settings: FileSettingsAdapter | None = None,
    open_dir_fn: Callable[[str], None] = start_file,
):
    try:
        ui_bridge = ui_bridge or file_ui_bridge
        processing_state = processing_state or file_processing_state
        settings = settings or _get_file_settings_store()
        runtime = _build_translate_result_runtime(
            engine=engine,
            setting_cache=settings.cache,
            ui_bridge=ui_bridge,
            processing_state=processing_state,
            settings=settings,
        )
        status_context = runtime.status_context
        processing_state.reset_mod_counter()

        runtime.ui_bridge.init_file_batch(f"Task Translate with {engine}", data_files)

        def is_still_active():
            return status_context.has_active_work(len(data_files))

        for i, file_path in enumerate(data_files):
            if not processing_state.is_file_processing():
                break

            try:
                result = runtime.stable_whisper_api.WhisperResult(file_path)
            except Exception:
                _update_status(status_context, "mod", i, "Parse Error")
                continue

            lang_src = to_language_name(result.language) or "auto"
            file_name = _slice_display_name(file_path, start=runtime.slice_start, end=runtime.slice_end)
            base_name = _build_base_export_name(
                datetime.now().strftime(runtime.settings.cache["export_format"]),
                file_name,
                lang_src,
                lang_target,
                "",
                engine,
            )

            format_dict = get_task_format("translated result", f"translated result from {lang_src} to {lang_target}", f"translated result with {engine}", f"translated result from {lang_src} to {lang_target} with {engine}")
            format_dict.update(get_task_format("tl res", f"tl res from {lang_src} to {lang_target}", f"tl res with {engine}", f"tl res from {lang_src} to {lang_target} with {engine}", short_only=True))
            export_plan = _build_export_plan(runtime.export_dir, base_name, format_dict)

            _update_status(status_context, "mod", i, "Translating please wait...")
            fail_status = WorkerFailure()
            
            _run_monitored_worker(
                run_translate_api,
                cancel_check=processing_state.is_file_processing,
                args=(result, engine, lang_src, lang_target, fail_status),
                kwargs=runtime.api_kwargs,
            )

            if fail_status.failed:
                _update_status(status_context, "mod", i, "Failed")
                continue

            processing_state.increment_mod_counter()
            save_output_stable_ts(split_res(result, runtime.settings.cache), export_plan.save_base_path, runtime.settings.cache["export_to"], runtime.settings, source_media_path=file_path)
            _update_status(status_context, "mod", i, "Translated")
            _save_export_plan_metadata(export_plan, {"meta_written_at": str(datetime.now()), "task": "Translate JSON", "time": time() - runtime.started_at})

        while processing_state.is_file_processing() and is_still_active():
            sleep(0.5)

        logger.info(f"Process TL JSON completed in {time() - runtime.started_at:.2f}s")
        if processing_state.mod_counter() > 0 and runtime.settings.cache["auto_open_dir_translate"]:
            open_dir_fn(runtime.export_dir)

    except Exception as e:
        logger.error(f"Process TL JSON error: {e}")
    finally:
        processing_state.disable_file_process()
        empty_torch_cuda_cache()
