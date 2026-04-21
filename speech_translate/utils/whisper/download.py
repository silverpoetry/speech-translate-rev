# pylint: disable=import-outside-toplevel, protected-access
import hashlib
import os
import urllib.request
from pathlib import Path
from threading import Thread
from time import sleep, time
from typing import Optional, Union, Literal, List, Dict

import huggingface_hub
import requests
from huggingface_hub.file_download import repo_folder_name
from loguru import logger

from speech_translate.linker import bc


def get_default_download_root():
    """Get the default download root"""
    return os.getenv("XDG_CACHE_HOME", os.path.join(os.path.expanduser("~"), ".cache", "whisper"))


def whisper_download_headless(
    model_name: str,
    url: str,
    download_root: str,
    cancel_func,
    after_func,
    failed_func,
):
    os.makedirs(download_root, exist_ok=True)
    expected_sha256 = url.split("/")[-2]
    download_target = os.path.join(download_root, os.path.basename(url))

    if os.path.exists(download_target) and not os.path.isfile(download_target):
        logger.error(f"{download_target} exists and is not a regular file")
        Thread(target=failed_func, daemon=True).start()
        return False

    if os.path.isfile(download_target):
        with open(download_target, "rb") as f:
            model_bytes = f.read()
        if hashlib.sha256(model_bytes).hexdigest() == expected_sha256:
            return download_target
        else:
            logger.warning(f"{download_target} exists, but the SHA256 checksum does not match; re-downloading the file")

    if bc.web_bridge is not None:
        bc.web_bridge.reset_task_state(title="Downloading Whisper Model")

    downloading = True
    success = False
    msg = ""

    try:
        with urllib.request.urlopen(url) as source, open(download_target, "wb") as output:
            buffer_size = 8192
            length = int(source.info().get("Content-Length"))
            length_in_mb = length / 1024 / 1024

            bytes_read = 0

            while True:
                if bc.cancel_dl:
                    logger.info("Download cancelled")
                    downloading = False
                    bc.cancel_dl = False
                    if bc.web_bridge is not None:
                        bc.web_bridge.finish_task("Download Cancelled")
                    return False

                buffer = source.read(buffer_size)
                if not buffer:
                    downloading = False
                    break

                output.write(buffer)
                bytes_read += len(buffer)
                percent = (bytes_read / length) * 100
                mb_downloaded = bytes_read / 1024 / 1024

                if bc.web_bridge is not None:
                    bc.web_bridge.update_task_message(f"Downloading {model_name} model ({mb_downloaded:.2f}/{length_in_mb:.2f} MB)")
                    bc.web_bridge.update_task_progress(percent)

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
        if bc.web_bridge is not None:
            bc.web_bridge.finish_task("Download Complete")
        if after_func:
            Thread(target=after_func, daemon=True).start()
        return True
    else:
        logger.info("Download failed")
        if bc.web_bridge is not None:
            bc.web_bridge.update_task_error(f"Download failed: {msg}")
        if failed_func:
            Thread(target=failed_func, daemon=True).start()
        return False


@huggingface_hub.utils.validate_hf_hub_args
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

from speech_translate.utils.helper import kill_thread

def faster_whisper_download_headless(
    model_name: str, repo_id: str, cache_dir: str, cancel_func, after_func, failed_func
):
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

    if bc.web_bridge is not None:
        bc.web_bridge.reset_task_state(title="Checking Faster Whisper Model")
        bc.web_bridge.update_task_message("Fetching model info please wait...")
        
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

    failed = False
    msg = ""
    finished = False

    def run_threaded():
        nonlocal failed, msg, finished
        try:
            snapshot_download(repo_id, **kwargs)
        except (
            huggingface_hub.utils.HfHubHTTPError,
            requests.exceptions.ConnectionError,
        ) as ee:
            logger.exception(ee)
            failed = True
            msg = str(ee)
        except Exception as e:
            logger.exception(e)
            failed = True
            msg = str(e)
        finally:
            finished = True

    threaded = Thread(target=run_threaded, daemon=True)
    threaded.start()

    def get_current_size():
        size = 0
        if not os.path.exists(storage_folder):
            return 0
        for root, dirs, files in os.walk(storage_folder):
            if 'snapshots' in root.split(os.sep) or 'refs' in root.split(os.sep):
                continue
            for f in files:
                filepath = os.path.join(root, f)
                if not os.path.islink(filepath):
                    size += os.path.getsize(filepath)
        return size

    while not finished:
        if bc.cancel_dl:
            kill_thread(threaded)
            finished = True
            bc.cancel_dl = False
            logger.info("Download Cancelled")
            if bc.web_bridge is not None:
                bc.web_bridge.finish_task("Download Cancelled")
            return

        sleep(0.1)
        if bc.web_bridge is not None:
            current_sz = get_current_size()
            if total_size > 0:
                # To prevent percent > 100 due to overhead bloat
                display_sz = min(current_sz, total_size)
                mb_downloaded = display_sz / 1024 / 1024
                length_in_mb = total_size / 1024 / 1024
                percent = (display_sz / total_size) * 100
                bc.web_bridge.update_task_message(f"Downloading {model_name} model ({mb_downloaded:.2f}/{length_in_mb:.2f} MB)")
                bc.web_bridge.update_task_progress(percent)
            else:
                mb_downloaded = current_sz / 1024 / 1024
                bc.web_bridge.update_task_message(f"Downloading {model_name} model ({mb_downloaded:.2f} MB / Unknown)")

    if success := not failed:
        logger.info("Download finished")
        if bc.web_bridge is not None:
            bc.web_bridge.finish_task("Download Complete")
        if after_func:
            Thread(target=after_func, daemon=True).start()
    else:
        logger.info("Download failed")
        if bc.web_bridge is not None:
            bc.web_bridge.update_task_error(f"Download failed: {msg}")
        if failed_func:
            Thread(target=failed_func, daemon=True).start()

    return success


# donwload function
def download_model(model_key, bridge=None, **kwargs):
    from faster_whisper.utils import _MODELS as FW_MODELS
    from whisper import _MODELS

    download_root = kwargs.pop("download_root", None)
    if download_root is None:
        download_root = get_default_download_root()

    use_faster_whisper = kwargs.pop("use_faster_whisper")
    model_id = _MODELS[model_key] if not use_faster_whisper else FW_MODELS[model_key]

    cancel_func = kwargs.pop("cancel_func", None)
    after_func = kwargs.pop("after_func", None)
    failed_func = kwargs.pop("failed_func", None)

    if not use_faster_whisper:
        return whisper_download_headless(model_key, model_id, download_root, cancel_func, after_func, failed_func)
    else:
        return faster_whisper_download_headless(model_key, model_id, download_root, cancel_func, after_func, failed_func)


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
