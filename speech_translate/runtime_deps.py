from __future__ import annotations

from functools import lru_cache
from importlib import import_module
from typing import Any


@lru_cache(maxsize=None)
def _import_runtime_dependency(module_name: str, package_name: str) -> Any:
    try:
        return import_module(module_name)
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised via callers
        raise RuntimeError(f"{package_name} is unavailable") from exc


def _optional_runtime_dependency(module_name: str, package_name: str) -> Any | None:
    try:
        return _import_runtime_dependency(module_name, package_name)
    except RuntimeError:
        return None


def get_torch() -> Any:
    return _import_runtime_dependency("torch", "torch")


def get_torchaudio() -> Any:
    return _import_runtime_dependency("torchaudio", "torchaudio")


def get_webrtcvad() -> Any:
    return _import_runtime_dependency("webrtcvad", "webrtcvad")


def get_stable_whisper() -> Any:
    return _import_runtime_dependency("stable_whisper", "stable-whisper")


@lru_cache(maxsize=1)
def _get_stable_whisper_utils() -> tuple[Any, Any]:
    stable_whisper_utils = _import_runtime_dependency("stable_whisper.utils", "stable-whisper")
    return stable_whisper_utils.isolate_useful_options, stable_whisper_utils.str_to_valid_type


def get_stable_whisper_utils() -> tuple[Any, Any]:
    return _get_stable_whisper_utils()


@lru_cache(maxsize=1)
def _get_whisper_decoding_options_type() -> Any:
    whisper_module = _import_runtime_dependency("whisper", "whisper")
    return getattr(whisper_module, "DecodingOptions")


def get_whisper_decoding_options_type() -> Any:
    return _get_whisper_decoding_options_type()


@lru_cache(maxsize=1)
def _get_faster_whisper_transcription_options_type() -> Any:
    faster_whisper_transcribe = _import_runtime_dependency("faster_whisper.transcribe", "faster-whisper")
    return getattr(faster_whisper_transcribe, "TranscriptionOptions")


def get_faster_whisper_transcription_options_type() -> Any:
    return _get_faster_whisper_transcription_options_type()


@lru_cache(maxsize=1)
def _get_faster_whisper_model_class() -> Any:
    faster_whisper_module = _import_runtime_dependency("faster_whisper", "faster-whisper")
    return getattr(faster_whisper_module, "WhisperModel")


def get_faster_whisper_model_class() -> Any:
    return _get_faster_whisper_model_class()


def try_get_requests() -> Any | None:
    return _optional_runtime_dependency("requests", "requests")


def get_huggingface_hub() -> Any:
    return _import_runtime_dependency("huggingface_hub", "huggingface_hub")


@lru_cache(maxsize=1)
def _get_huggingface_repo_folder_name() -> Any:
    file_download = _import_runtime_dependency("huggingface_hub.file_download", "huggingface_hub")
    return getattr(file_download, "repo_folder_name")


def get_huggingface_repo_folder_name() -> Any:
    return _get_huggingface_repo_folder_name()


@lru_cache(maxsize=1)
def _get_whisper_model_registry() -> dict[str, str]:
    whisper_module = _import_runtime_dependency("whisper", "whisper")
    return dict(getattr(whisper_module, "_MODELS"))


def get_whisper_model_registry() -> dict[str, str]:
    return dict(_get_whisper_model_registry())


@lru_cache(maxsize=1)
def _get_faster_whisper_model_registry() -> dict[str, str]:
    faster_whisper_utils = _import_runtime_dependency("faster_whisper.utils", "faster-whisper")
    return dict(getattr(faster_whisper_utils, "_MODELS"))


def get_faster_whisper_model_registry() -> dict[str, str]:
    return dict(_get_faster_whisper_model_registry())


@lru_cache(maxsize=1)
def _get_whisper_to_language_code_map() -> dict[str, str]:
    tokenizer = _import_runtime_dependency("whisper.tokenizer", "whisper")
    return dict(getattr(tokenizer, "TO_LANGUAGE_CODE"))


def get_whisper_to_language_code() -> dict[str, str]:
    return dict(_get_whisper_to_language_code_map())


def torch_from_numpy(array: Any) -> Any:
    return get_torch().from_numpy(array)


def empty_torch_cuda_cache() -> None:
    try:
        get_torch().cuda.empty_cache()
    except RuntimeError:
        return
    except AttributeError:
        return
    except Exception:
        return
