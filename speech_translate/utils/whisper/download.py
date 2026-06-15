# pylint: disable=import-outside-toplevel, protected-access
from __future__ import annotations

import hashlib
import os
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from time import time
from typing import Any, Callable, Dict, List, Literal, Optional, Union

from speech_translate.linker import bc
from speech_translate.log_helpers import logger
from speech_translate.runtime_deps import (
    get_faster_whisper_model_registry,
    get_huggingface_hub,
    get_huggingface_repo_folder_name,
    get_whisper_model_registry,
    try_get_requests,
)
from speech_translate.web_bridge_runtime import WebBridgeRegistry, web_bridge_registry
from speech_translate.utils.helper import kill_thread
from speech_translate.utils.whisper.download_runtime import (
    TaskReporter,
    build_download_progress_snapshot,
    monitor_threaded_download,
    start_optional_callback,
)
from speech_translate.utils.whisper.paths import get_default_download_root


@dataclass(frozen=True)
class HuggingFaceDownloadRuntime:
    hub: Any
    repo_folder_name: Callable[..., str]


@dataclass(frozen=True)
class DownloadExecutionHooks:
    reporter: TaskReporter
    cancel_requested: Callable[[], bool]
    clear_cancel_requested: Callable[[], None]
    start_callback: Callable[[Callable | None], None]


@dataclass(frozen=True)
class DownloadBridgeAdapter:
    bridge: Any | None = None
    bridge_registry: WebBridgeRegistry = field(default_factory=lambda: web_bridge_registry)

    def resolve(self) -> Any | None:
        return self.bridge_registry.get() if self.bridge is None else self.bridge


@dataclass(frozen=True)
class DownloadCancellationAdapter:
    state: object = bc

    def cancel_requested(self) -> bool:
        return bool(self.state.cancel_dl)

    def clear_cancel_requested(self) -> None:
        setattr(self.state, "cancel_dl", False)


download_bridge = DownloadBridgeAdapter()
download_cancellation = DownloadCancellationAdapter()


def _build_bridge_task_reporter(bridge: Any | None = None, bridge_adapter: DownloadBridgeAdapter | None = None) -> TaskReporter:
    bridge_adapter = bridge_adapter or DownloadBridgeAdapter(bridge=bridge)
    web_bridge = bridge_adapter.resolve()
    if web_bridge is None:
        return TaskReporter()
    return TaskReporter(
        reset_task_state=lambda title: web_bridge.reset_task_state(title=title),
        update_task_message=web_bridge.update_task_message,
        update_task_progress=web_bridge.update_task_progress,
        finish_task=web_bridge.finish_task,
        update_task_error=web_bridge.update_task_error,
    )


def _build_huggingface_download_runtime() -> HuggingFaceDownloadRuntime:
    return HuggingFaceDownloadRuntime(
        hub=get_huggingface_hub(),
        repo_folder_name=get_huggingface_repo_folder_name(),
    )


def _build_download_execution_hooks(
    *,
    reporter: TaskReporter | None = None,
    bridge: Any | None = None,
    bridge_adapter: DownloadBridgeAdapter | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    clear_cancel_requested: Callable[[], None] | None = None,
    cancellation_adapter: DownloadCancellationAdapter | None = None,
    callback_starter: Callable[[Callable | None], None] = start_optional_callback,
) -> DownloadExecutionHooks:
    bridge_adapter = bridge_adapter or DownloadBridgeAdapter(bridge=bridge)
    cancellation_adapter = cancellation_adapter or download_cancellation
    return DownloadExecutionHooks(
        reporter=reporter or _build_bridge_task_reporter(bridge_adapter=bridge_adapter),
        cancel_requested=cancel_requested or cancellation_adapter.cancel_requested,
        clear_cancel_requested=clear_cancel_requested or cancellation_adapter.clear_cancel_requested,
        start_callback=callback_starter,
    )


def _calculate_sha256(file_path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    hasher = hashlib.sha256()
    with open(file_path, "rb") as model_file:
        for chunk in iter(lambda: model_file.read(chunk_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _get_requests_connection_error_types(requests_module=None) -> tuple[type[BaseException], ...]:
    requests_module = try_get_requests() if requests_module is None else requests_module
    if requests_module is None:
        return ()

    exceptions_mod = getattr(requests_module, "exceptions", None)
    connection_error = getattr(exceptions_mod, "ConnectionError", None)
    if isinstance(connection_error, type) and issubclass(connection_error, BaseException):
        return (connection_error,)
    return ()


def _read_content_length(headers: Any) -> int:
    raw_length = None
    try:
        raw_length = headers.get("Content-Length")
    except Exception:
        raw_length = None

    try:
        return max(0, int(raw_length))
    except (TypeError, ValueError):
        return 0


def _resolve_faster_whisper_storage_folder(
    cache_dir: str | Path,
    repo_id: str,
    *,
    repo_folder_name: Callable[..., str],
) -> Path:
    return Path(cache_dir) / repo_folder_name(repo_id=repo_id, repo_type="model")


def _is_complete_model_dir(model_dir: Path) -> bool:
    if not model_dir.exists() or not model_dir.is_dir():
        return False

    has_config = (model_dir / "config.json").exists()
    has_weights = (model_dir / "model.bin").exists() or (model_dir / "model.safetensors").exists()
    if not (has_config and has_weights):
        return False

    if any(model_dir.rglob("*.incomplete")):
        return False

    return True


def _resolve_whisper_model_url(model_key: str) -> str:
    model_registry = get_whisper_model_registry()
    try:
        return model_registry[model_key]
    except KeyError as exc:
        available = ", ".join(sorted(model_registry.keys()))
        raise RuntimeError(f"Model {model_key} not found; available models = [{available}]") from exc


def _resolve_faster_whisper_repo_id(model_key: str) -> str:
    model_registry = get_faster_whisper_model_registry()
    repo_id = model_registry.get(model_key)
    if repo_id is None:
        available = ", ".join(sorted(model_registry.keys()))
        raise ValueError(f"Invalid model size '{model_key}', expected one of: {available}")
    return repo_id


def _verify_faster_whisper_snapshot_cache(model_key: str, cache_dir: str | Path) -> bool:
    repo_id = _resolve_faster_whisper_repo_id(model_key)

    local_model_dir = Path(cache_dir) / f"faster-whisper-{model_key}"
    if _is_complete_model_dir(local_model_dir):
        return True

    try:
        repo_folder_name = get_huggingface_repo_folder_name()
    except RuntimeError:
        return False

    storage_folder = _resolve_faster_whisper_storage_folder(
        cache_dir,
        repo_id,
        repo_folder_name=repo_folder_name,
    )
    snapshot_root = storage_folder / "snapshots"

    if not snapshot_root.exists():
        return False

    snapshot_dirs = [path for path in snapshot_root.iterdir() if path.is_dir()]
    if not snapshot_dirs:
        return False

    snapshot_dirs.sort(key=lambda path: path.stat().st_mtime, reverse=True)

    for snapshot in snapshot_dirs:
        if _is_complete_model_dir(snapshot):
            return True

    return False


def whisper_download_headless(
    model_name: str,
    url: str,
    download_root: str,
    cancel_func,
    after_func,
    failed_func,
    *,
    reporter: TaskReporter | None = None,
    bridge: Any | None = None,
    hooks: DownloadExecutionHooks | None = None,
    progress_callback=None,
    progress_floor: float = 0.0,
    progress_ceiling: float = 100.0,
):
    hooks = hooks or _build_download_execution_hooks(reporter=reporter, bridge=bridge)
    reporter = hooks.reporter
    os.makedirs(download_root, exist_ok=True)
    expected_sha256 = url.split("/")[-2]
    download_target = os.path.join(download_root, os.path.basename(url))

    if os.path.exists(download_target) and not os.path.isfile(download_target):
        logger.error(f"{download_target} exists and is not a regular file")
        start_optional_callback(failed_func)
        return False

    if os.path.isfile(download_target):
        if _calculate_sha256(download_target) == expected_sha256:
            return download_target
        logger.warning(f"{download_target} exists, but the SHA256 checksum does not match; re-downloading the file")

    reporter.reset_task_state("Downloading Whisper Model")

    success = False
    msg = ""
    started_at = 0.0
    previous_bytes = 0
    previous_time = 0.0

    try:
        download_hasher = hashlib.sha256()
        with urllib.request.urlopen(url) as source, open(download_target, "wb") as output:
            buffer_size = 8192
            length = _read_content_length(source.info())
            length_in_mb = length / 1024 / 1024 if length > 0 else 0.0

            bytes_read = 0
            started_at = previous_time = time()

            while True:
                if hooks.cancel_requested():
                    logger.info("Download cancelled")
                    hooks.clear_cancel_requested()
                    reporter.finish_task("Download Cancelled")
                    hooks.start_callback(cancel_func)
                    return False

                buffer = source.read(buffer_size)
                if not buffer:
                    break

                output.write(buffer)
                download_hasher.update(buffer)
                bytes_read += len(buffer)
                current_time = time()
                snapshot = build_download_progress_snapshot(
                    current_bytes=bytes_read,
                    total_bytes=length,
                    started_at=started_at,
                    previous_bytes=previous_bytes,
                    previous_time=previous_time,
                    current_time=current_time,
                    progress_floor=progress_floor,
                    progress_ceiling=progress_ceiling,
                    allow_time_fallback=length <= 0,
                )
                previous_bytes = bytes_read
                previous_time = current_time

                if length > 0:
                    reporter.update_task_message(
                        f"Downloading {model_name} model ({bytes_read / 1024 / 1024:.2f}/{length_in_mb:.2f} MB)"
                    )
                else:
                    reporter.update_task_message(
                        f"Downloading {model_name} model ({bytes_read / 1024 / 1024:.2f} MB / Unknown)"
                    )
                reporter.update_task_progress(snapshot.progress)
                if progress_callback is not None:
                    progress_callback(snapshot)

        if download_hasher.hexdigest() != expected_sha256:
            logger.error("Model has been downloaded but the SHA256 checksum does not match. Please retry loading the model.")
            msg = "SHA256 mismatch"
            success = False
        else:
            success = True

    except Exception as exc:
        logger.exception(exc)
        msg = str(exc)
        success = False

    if success:
        logger.info("Download finished successfully")
        reporter.finish_task("Download Complete")
        hooks.start_callback(after_func)
        return True

    logger.info("Download failed")
    reporter.update_task_error(f"Download failed: {msg}")
    hooks.start_callback(failed_func)
    return False


def snapshot_download(
    repo_id: str,
    *,
    repo_type: Optional[str] = None,
    revision: Optional[str] = None,
    endpoint: Optional[str] = None,
    cache_dir: Union[str, Path, None] = None,
    local_dir: Union[str, Path, None] = None,
    local_dir_use_symlinks: Union[bool, Literal["auto"]] = "auto",
    library_name: Optional[str] = None,
    library_version: Optional[str] = None,
    user_agent: Optional[Union[Dict, str]] = None,
    proxies: Optional[Dict] = None,
    etag_timeout: float = 5,
    resume_download: bool = False,
    force_download: bool = False,
    token: Optional[Union[bool, str]] = None,
    local_files_only: bool = False,
    allow_patterns: Optional[Union[List[str], str]] = None,
    ignore_patterns: Optional[Union[List[str], str]] = None,
    tqdm_class=None,
    hf_runtime: HuggingFaceDownloadRuntime | None = None,
) -> str:
    hf_runtime = hf_runtime or _build_huggingface_download_runtime()
    return hf_runtime.hub.snapshot_download(
        repo_id,
        repo_type=repo_type,
        revision=revision,
        endpoint=endpoint,
        cache_dir=cache_dir,
        local_dir=local_dir,
        local_dir_use_symlinks=local_dir_use_symlinks,
        library_name=library_name,
        library_version=library_version,
        user_agent=user_agent,
        proxies=proxies,
        etag_timeout=etag_timeout,
        resume_download=resume_download,
        force_download=force_download,
        token=token,
        local_files_only=local_files_only,
        allow_patterns=allow_patterns,
        ignore_patterns=ignore_patterns,
        tqdm_class=tqdm_class,
    )


def faster_whisper_download_headless(
    model_name: str,
    repo_id: str,
    cache_dir: str,
    cancel_func,
    after_func,
    failed_func,
    *,
    reporter: TaskReporter | None = None,
    bridge: Any | None = None,
    hooks: DownloadExecutionHooks | None = None,
    progress_callback=None,
    progress_floor: float = 0.0,
    progress_ceiling: float = 100.0,
    hf_runtime: HuggingFaceDownloadRuntime | None = None,
):
    hf_runtime = hf_runtime or _build_huggingface_download_runtime()
    huggingface_hub = hf_runtime.hub
    hooks = hooks or _build_download_execution_hooks(reporter=reporter, bridge=bridge)
    reporter = hooks.reporter
    logger.debug("Downloading model from Hugging Face Hub")
    os.makedirs(cache_dir, exist_ok=True)

    storage_folder = str(
        _resolve_faster_whisper_storage_folder(
            cache_dir,
            repo_id,
            repo_folder_name=hf_runtime.repo_folder_name,
        )
    )
    allow_patterns = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
    kwargs = {
        "local_files_only": False,
        "allow_patterns": allow_patterns,
        "resume_download": True,
        "cache_dir": cache_dir,
        "local_dir_use_symlinks": True,
    }

    reporter.reset_task_state("Checking Faster Whisper Model")
    reporter.update_task_message("Fetching model info please wait...")

    try:
        api = huggingface_hub.HfApi()
        repo_info = api.repo_info(repo_id=repo_id, repo_type="model", files_metadata=True)
        filtered = list(
            huggingface_hub.utils.filter_repo_objects(
                items=[sibling.rfilename for sibling in repo_info.siblings],
                allow_patterns=allow_patterns,
                ignore_patterns=None,
            )
        )
        total_size = sum(sibling.size for sibling in repo_info.siblings if sibling.rfilename in filtered and sibling.size is not None)
    except Exception as exc:
        logger.warning(f"Failed to fetch total size: {exc}")
        total_size = 0

    request_error_types = _get_requests_connection_error_types()

    def _download() -> None:
        try:
            snapshot_download(repo_id, hf_runtime=hf_runtime, **kwargs)
        except ((huggingface_hub.utils.HfHubHTTPError,) + request_error_types) as exc:
            logger.exception(exc)
            raise

    def _handle_progress(snapshot) -> None:
        if total_size > 0:
            display_sz = min(snapshot.current_bytes, total_size)
            reporter.update_task_message(
                f"Downloading {model_name} model ({display_sz / 1024 / 1024:.2f}/{total_size / 1024 / 1024:.2f} MB)"
            )
        else:
            reporter.update_task_message(f"Downloading {model_name} model ({snapshot.current_bytes / 1024 / 1024:.2f} MB / Unknown)")
        reporter.update_task_progress(snapshot.progress)
        if progress_callback is not None:
            progress_callback(snapshot)

    monitor_result = monitor_threaded_download(
        download_fn=_download,
        observe_path=storage_folder,
        total_bytes=total_size,
        on_progress=_handle_progress,
        cancel_requested=hooks.cancel_requested,
        cancel_handler=kill_thread,
        progress_floor=progress_floor,
        progress_ceiling=progress_ceiling,
    )

    if monitor_result.cancelled:
        hooks.clear_cancel_requested()
        logger.info("Download Cancelled")
        reporter.finish_task("Download Cancelled")
        hooks.start_callback(cancel_func)
        return False

    if success := monitor_result.error is None:
        logger.info("Download finished")
        reporter.finish_task("Download Complete")
        hooks.start_callback(after_func)
    else:
        logger.info("Download failed")
        reporter.update_task_error(f"Download failed: {monitor_result.error}")
        hooks.start_callback(failed_func)

    return success


def download_model(model_key, bridge=None, **kwargs):
    download_root = kwargs.pop("download_root", None)
    if download_root is None:
        download_root = get_default_download_root()

    use_faster_whisper = kwargs.pop("use_faster_whisper")
    reporter = kwargs.pop("reporter", None)
    progress_callback = kwargs.pop("progress_callback", None)
    progress_floor = float(kwargs.pop("progress_floor", 0.0))
    progress_ceiling = float(kwargs.pop("progress_ceiling", 100.0))
    cancel_requested = kwargs.pop("cancel_requested", None)
    clear_cancel_requested = kwargs.pop("clear_cancel_requested", None)
    bridge_adapter = kwargs.pop("bridge_adapter", None)
    cancellation_adapter = kwargs.pop("cancellation_adapter", None)
    callback_starter = kwargs.pop("callback_starter", start_optional_callback)

    cancel_func = kwargs.pop("cancel_func", None)
    after_func = kwargs.pop("after_func", None)
    failed_func = kwargs.pop("failed_func", None)
    hooks = _build_download_execution_hooks(
        reporter=reporter,
        bridge=bridge,
        bridge_adapter=bridge_adapter,
        cancel_requested=cancel_requested,
        clear_cancel_requested=clear_cancel_requested,
        cancellation_adapter=cancellation_adapter,
        callback_starter=callback_starter,
    )

    if not use_faster_whisper:
        return whisper_download_headless(
            model_key,
            _resolve_whisper_model_url(model_key),
            download_root,
            cancel_func,
            after_func,
            failed_func,
            hooks=hooks,
            progress_callback=progress_callback,
            progress_floor=progress_floor,
            progress_ceiling=progress_ceiling,
        )

    hf_runtime = _build_huggingface_download_runtime()
    return faster_whisper_download_headless(
        model_key,
        _resolve_faster_whisper_repo_id(model_key),
        download_root,
        cancel_func,
        after_func,
        failed_func,
        hooks=hooks,
        progress_callback=progress_callback,
        progress_floor=progress_floor,
        progress_ceiling=progress_ceiling,
        hf_runtime=hf_runtime,
    )


def verify_model_whisper(model_key, download_root=None):
    if download_root is None:
        download_root = get_default_download_root()

    model_file = os.path.join(download_root, model_key + ".pt")
    if not os.path.exists(model_file):
        return False

    expected_sha256 = _resolve_whisper_model_url(model_key).split("/")[-2]
    return _calculate_sha256(model_file) == expected_sha256


def verify_model_faster_whisper(model_key: str, cache_dir) -> bool:
    return _verify_faster_whisper_snapshot_cache(model_key, cache_dir)
