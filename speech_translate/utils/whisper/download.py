# pylint: disable=import-outside-toplevel, protected-access
import hashlib
import os
import urllib.request
from pathlib import Path
from time import time
from typing import Dict, List, Literal, Optional, Union

from speech_translate.log_helpers import logger
from speech_translate.utils.whisper.paths import get_default_download_root
from speech_translate.utils.whisper.download_runtime import (
    TaskReporter,
    build_download_progress_snapshot,
    monitor_threaded_download,
    start_optional_callback,
)

try:
    import huggingface_hub
    from huggingface_hub.file_download import repo_folder_name
    _validate_hf_hub_args = huggingface_hub.utils.validate_hf_hub_args
except ModuleNotFoundError:  # pragma: no cover - optional runtime dependency fallback
    huggingface_hub = None  # type: ignore[assignment]
    repo_folder_name = None  # type: ignore[assignment]

    def _validate_hf_hub_args(func):
        return func

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - optional runtime dependency fallback
    requests = None  # type: ignore[assignment]

from speech_translate.linker import bc
from speech_translate.utils.helper import kill_thread


def _build_bridge_task_reporter() -> TaskReporter:
    web_bridge = bc.web_bridge
    if web_bridge is None:
        return TaskReporter()
    return TaskReporter(
        reset_task_state=lambda title: web_bridge.reset_task_state(title=title),
        update_task_message=web_bridge.update_task_message,
        update_task_progress=web_bridge.update_task_progress,
        finish_task=web_bridge.finish_task,
        update_task_error=web_bridge.update_task_error,
    )


def _get_requests_connection_error_types() -> tuple[type[BaseException], ...]:
    exceptions_mod = getattr(requests, "exceptions", None)
    connection_error = getattr(exceptions_mod, "ConnectionError", None)
    if isinstance(connection_error, type) and issubclass(connection_error, BaseException):
        return (connection_error,)
    return ()


def whisper_download_headless(
    model_name: str,
    url: str,
    download_root: str,
    cancel_func,
    after_func,
    failed_func,
    *,
    reporter: TaskReporter | None = None,
    progress_callback=None,
    progress_floor: float = 0.0,
    progress_ceiling: float = 100.0,
):
    reporter = reporter or _build_bridge_task_reporter()
    os.makedirs(download_root, exist_ok=True)
    expected_sha256 = url.split("/")[-2]
    download_target = os.path.join(download_root, os.path.basename(url))

    if os.path.exists(download_target) and not os.path.isfile(download_target):
        logger.error(f"{download_target} exists and is not a regular file")
        start_optional_callback(failed_func)
        return False

    if os.path.isfile(download_target):
        with open(download_target, "rb") as f:
            model_bytes = f.read()
        if hashlib.sha256(model_bytes).hexdigest() == expected_sha256:
            return download_target
        else:
            logger.warning(f"{download_target} exists, but the SHA256 checksum does not match; re-downloading the file")

    reporter.reset_task_state("Downloading Whisper Model")

    success = False
    msg = ""
    started_at = 0.0
    previous_bytes = 0
    previous_time = 0.0

    try:
        with urllib.request.urlopen(url) as source, open(download_target, "wb") as output:
            buffer_size = 8192
            length = int(source.info().get("Content-Length"))
            length_in_mb = length / 1024 / 1024

            bytes_read = 0
            started_at = previous_time = time()

            while True:
                if bc.cancel_dl:
                    logger.info("Download cancelled")
                    bc.cancel_dl = False
                    reporter.finish_task("Download Cancelled")
                    start_optional_callback(cancel_func)
                    return False

                buffer = source.read(buffer_size)
                if not buffer:
                    break

                output.write(buffer)
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
                    allow_time_fallback=False,
                )
                previous_bytes = bytes_read
                previous_time = current_time

                reporter.update_task_message(
                    f"Downloading {model_name} model ({bytes_read / 1024 / 1024:.2f}/{length_in_mb:.2f} MB)"
                )
                reporter.update_task_progress(snapshot.progress)
                if progress_callback is not None:
                    progress_callback(snapshot)

        model_bytes = open(download_target, "rb").read()
        if hashlib.sha256(model_bytes).hexdigest() != expected_sha256:
            logger.error("Model has been downloaded but the SHA256 checksum does not match. Please retry loading the model.")
            msg = "SHA256 mismatch"
            success = False
        else:
            success = True

    except Exception as e:
        logger.exception(e)
        msg = str(e)
        success = False
    
    if success:
        logger.info("Download finished successfully")
        reporter.finish_task("Download Complete")
        start_optional_callback(after_func)
        return True
    else:
        logger.info("Download failed")
        reporter.update_task_error(f"Download failed: {msg}")
        start_optional_callback(failed_func)
        return False


@_validate_hf_hub_args
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
) -> str:
    """Wrapper around huggingface_hub.snapshot_download to download correctly."""
    if huggingface_hub is None:
        raise RuntimeError("huggingface_hub is unavailable")
    return huggingface_hub.snapshot_download(
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
    progress_callback=None,
    progress_floor: float = 0.0,
    progress_ceiling: float = 100.0,
):
    if huggingface_hub is None or repo_folder_name is None:
        raise RuntimeError("huggingface_hub is unavailable")
    reporter = reporter or _build_bridge_task_reporter()
    logger.debug("Downloading model from Hugging Face Hub")
    os.makedirs(cache_dir, exist_ok=True)

    storage_folder = os.path.join(cache_dir, repo_folder_name(repo_id=repo_id, repo_type="model"))
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
        filtered = list(huggingface_hub.utils.filter_repo_objects(
            items=[f.rfilename for f in repo_info.siblings],
            allow_patterns=allow_patterns,
            ignore_patterns=None
        ))
        total_size = sum(f.size for f in repo_info.siblings if f.rfilename in filtered and f.size is not None)
    except Exception as e:
        logger.warning(f"Failed to fetch total size: {e}")
        total_size = 0

    request_error_types = _get_requests_connection_error_types()

    def _download() -> None:
        try:
            snapshot_download(repo_id, **kwargs)
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
        cancel_requested=lambda: bool(bc.cancel_dl),
        cancel_handler=kill_thread,
        progress_floor=progress_floor,
        progress_ceiling=progress_ceiling,
    )

    if monitor_result.cancelled:
        bc.cancel_dl = False
        logger.info("Download Cancelled")
        reporter.finish_task("Download Cancelled")
        start_optional_callback(cancel_func)
        return False

    if success := monitor_result.error is None:
        logger.info("Download finished")
        reporter.finish_task("Download Complete")
        start_optional_callback(after_func)
    else:
        logger.info("Download failed")
        reporter.update_task_error(f"Download failed: {monitor_result.error}")
        start_optional_callback(failed_func)

    return success


# donwload function
def download_model(model_key, bridge=None, **kwargs):
    if huggingface_hub is None:
        raise RuntimeError("huggingface_hub is unavailable")
    from faster_whisper.utils import _MODELS as FW_MODELS
    from whisper import _MODELS

    download_root = kwargs.pop("download_root", None)
    if download_root is None:
        download_root = get_default_download_root()

    use_faster_whisper = kwargs.pop("use_faster_whisper")
    reporter = kwargs.pop("reporter", None)
    progress_callback = kwargs.pop("progress_callback", None)
    progress_floor = float(kwargs.pop("progress_floor", 0.0))
    progress_ceiling = float(kwargs.pop("progress_ceiling", 100.0))
    model_id = _MODELS[model_key] if not use_faster_whisper else FW_MODELS[model_key]

    cancel_func = kwargs.pop("cancel_func", None)
    after_func = kwargs.pop("after_func", None)
    failed_func = kwargs.pop("failed_func", None)

    if not use_faster_whisper:
        return whisper_download_headless(
            model_key,
            model_id,
            download_root,
            cancel_func,
            after_func,
            failed_func,
            reporter=reporter,
            progress_callback=progress_callback,
            progress_floor=progress_floor,
            progress_ceiling=progress_ceiling,
        )
    else:
        return faster_whisper_download_headless(
            model_key,
            model_id,
            download_root,
            cancel_func,
            after_func,
            failed_func,
            reporter=reporter,
            progress_callback=progress_callback,
            progress_floor=progress_floor,
            progress_ceiling=progress_ceiling,
        )


# verify downloaded model sha
def verify_model_whisper(model_key, download_root=None):
    from whisper import _MODELS, available_models
    if download_root is None:
        download_root = get_default_download_root()

    if model_key not in _MODELS:
        raise RuntimeError(f"Model {model_key} not found; available models = {available_models()}")

    model_file = os.path.join(download_root, model_key + ".pt")
    if not os.path.exists(model_file):
        return False

    expected_sha256 = _MODELS[model_key].split("/")[-2]

    model_bytes = open(model_file, "rb").read()
    return hashlib.sha256(model_bytes).hexdigest() == expected_sha256


def verify_model_faster_whisper(model_key: str, cache_dir) -> bool:
    if huggingface_hub is None or repo_folder_name is None:
        raise RuntimeError("huggingface_hub is unavailable")
    from faster_whisper.utils import _MODELS as FW_MODELS
    repo_id = FW_MODELS.get(model_key)
    if repo_id is None:
        raise ValueError(f"Invalid model size '{model_key}', expected one of: {', '.join(FW_MODELS.keys())}")

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

    local_model_dir = Path(cache_dir) / f"faster-whisper-{model_key}"
    if _is_complete_model_dir(local_model_dir):
        return True

    storage_folder = Path(cache_dir) / repo_folder_name(repo_id=repo_id, repo_type="model")
    snapshot_root = storage_folder / "snapshots"

    if not snapshot_root.exists():
        return False

    snapshot_dirs = [p for p in snapshot_root.iterdir() if p.is_dir()]
    if not snapshot_dirs:
        return False

    snapshot_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    for snapshot in snapshot_dirs:
        if _is_complete_model_dir(snapshot):
            return True

    return False
