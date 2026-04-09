# pylint: disable=import-outside-toplevel, protected-access
import hashlib
import os
from pathlib import Path

from huggingface_hub.file_download import repo_folder_name
from loguru import logger

from speech_translate.ui.custom.download import faster_whisper_download_with_progress_gui, whisper_download_with_progress_gui


# donwload function
def download_model(model_key, root_win, **kwargs):
    """Download a model from the official model repository

    Parameters
    ----------
    model_key : str
        one of the official model keys
    download_root: str
        path to download the model files; by default, it uses "~/.cache/whisper"
    in_memory: bool
        whether to preload the model weights into host memory

    Returns
    -------
    model_bytes : bytes
        the model checkpoint as a byte string
    """
    from faster_whisper.utils import _MODELS as FW_MODELS
    from whisper import _MODELS

    download_root = kwargs.pop("download_root", None)
    if download_root is None:
        download_root = get_default_download_root()

    use_faster_whisper = kwargs.pop("use_faster_whisper")
    model_id = _MODELS[model_key] if not use_faster_whisper else FW_MODELS[model_key]

    # call different download function
    if not use_faster_whisper:
        return whisper_download_with_progress_gui(root_win, model_key, model_id, download_root, **kwargs)
    else:
        return faster_whisper_download_with_progress_gui(root_win, model_key, model_id, download_root, **kwargs)


# verify downloaded model sha
def verify_model_whisper(model_key, download_root=None):
    """Verify the SHA256 checksum of a downloaded model

    Parameters
    ----------
    model_key : str
        one of the official model names listed by `whisper.available_models()`
    download_root: str
        path to download the model files; by default, it uses "~/.cache/whisper"

    Returns
    -------
    bool
        True if the model is already downloaded
    """
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
    """
    Verify downloaded faster-whisper model using local Hugging Face cache structure.

    This check intentionally avoids online calls so UI model checks stay responsive.

    Parameters
    ----------
    model_key : str
        The key of the model
    cache_dir : _type_
        The cache directory

    Returns
    -------
    bool
        True if the model is already downloaded

    Raises
    ------
    ValueError
        If the model key is invalid
    """
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

    # Preferred local_dir layout used by our web download flow.
    local_model_dir = Path(cache_dir) / f"faster-whisper-{model_key}"
    if _is_complete_model_dir(local_model_dir):
        return True

    storage_folder = Path(cache_dir) / repo_folder_name(repo_id=repo_id, repo_type="model")
    snapshot_root = storage_folder / "snapshots"

    # snapshots directory must exist for a valid local model cache.
    if not snapshot_root.exists():
        return False

    snapshot_dirs = [p for p in snapshot_root.iterdir() if p.is_dir()]
    if not snapshot_dirs:
        return False

    # Prefer latest snapshot first.
    snapshot_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    # Validate presence of essential files in at least one snapshot.
    # Do not scan global blobs for lock files because stale locks from other models
    # can cause false negatives.
    for snapshot in snapshot_dirs:
        if _is_complete_model_dir(snapshot):
            return True

    return False


# get default download root
def get_default_download_root():
    """Get the default download root

    Returns
    -------
    str
        the default download root
    """
    return os.getenv("XDG_CACHE_HOME", os.path.join(os.path.expanduser("~"), ".cache", "whisper"))
