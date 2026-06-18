from __future__ import annotations

from speech_translate.utils.whisper.helper import model_select_dict, model_values


MODEL_DISPLAY_BY_KEY = {model_key: display_name for display_name, model_key in model_select_dict.items()}


def normalize_model_key(value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if normalized in model_select_dict:
        return model_select_dict[normalized]
    if normalized in model_values:
        return normalized
    return normalized


def resolve_model_display_name(model_key: object) -> str:
    normalized_key = normalize_model_key(model_key)
    if not normalized_key:
        return ""
    return MODEL_DISPLAY_BY_KEY.get(normalized_key, normalized_key)


__all__ = [
    "MODEL_DISPLAY_BY_KEY",
    "normalize_model_key",
    "resolve_model_display_name",
]
