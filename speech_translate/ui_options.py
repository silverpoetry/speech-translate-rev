from __future__ import annotations

from typing import Mapping

from speech_translate.utils.whisper.helper import model_select_dict


INPUT_MODE_OPTIONS = ["mic", "speaker"]
MODEL_BACKEND_OPTIONS = ["whisper", "faster-whisper"]
TRANSLATION_ENGINE_OPTIONS = [
    "Selenium Chrome Translate",
    "Google Translate",
    "MyMemoryTranslator",
    "LibreTranslate",
]
IMPORT_ENGINE_OPTIONS = [*TRANSLATION_ENGINE_OPTIONS, *list(model_select_dict.keys())]


def resolve_model_backend(settings_snapshot: Mapping[str, object]) -> str:
    return "faster-whisper" if bool(settings_snapshot.get("use_faster_whisper", True)) else "whisper"


__all__ = [
    "IMPORT_ENGINE_OPTIONS",
    "INPUT_MODE_OPTIONS",
    "MODEL_BACKEND_OPTIONS",
    "TRANSLATION_ENGINE_OPTIONS",
    "resolve_model_backend",
]
