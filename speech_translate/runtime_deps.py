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


def get_torch() -> Any:
    return _import_runtime_dependency("torch", "torch")


def get_torchaudio() -> Any:
    return _import_runtime_dependency("torchaudio", "torchaudio")


def get_webrtcvad() -> Any:
    return _import_runtime_dependency("webrtcvad", "webrtcvad")


def get_stable_whisper() -> Any:
    return _import_runtime_dependency("stable_whisper", "stable-whisper")


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
