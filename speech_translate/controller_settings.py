from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, cast

from speech_translate.controller_protocols import JsonDict
from speech_translate.model_selection import normalize_model_key as normalize_shared_model_key
from speech_translate.utils.types import SettingDict
from speech_translate.utils.whisper.helper import model_values


NormalizeName = Callable[[str], str]


@dataclass(frozen=True)
class RecordingControllerSettings:
    snapshot: SettingDict
    device: str
    lang_source: str
    lang_target: str
    engine: str
    model_name_tc: str
    is_tc: bool
    is_tl: bool
    selenium_auto_close_on_task_done: bool

    @property
    def engine_is_whisper(self) -> bool:
        return self.engine in model_values

    @property
    def should_auto_close_selenium(self) -> bool:
        return self.is_tl and self.engine == "Selenium Chrome Translate" and self.selenium_auto_close_on_task_done


@dataclass(frozen=True)
class RuntimeModelLoadSettings:
    snapshot: SettingDict
    model_key: str
    engine: str
    transcribe_enabled: bool
    translate_enabled: bool
    tl_engine_whisper: bool


@dataclass(frozen=True)
class SeleniumSettings:
    compact_level: int
    z_order_mode: str
    auto_close_on_task_done: bool
    chrome_user_data_dir: str

    def as_settings_updates(self) -> dict[str, object]:
        return {
            "selenium_compact_level": self.compact_level,
            "selenium_z_order_mode": self.z_order_mode,
            "selenium_auto_close_on_task_done": self.auto_close_on_task_done,
            "selenium_chrome_user_data_dir": self.chrome_user_data_dir,
        }


def _copy_settings_snapshot(settings_snapshot: Mapping[str, object]) -> SettingDict:
    return cast(SettingDict, dict(settings_snapshot))


def _normalize_compact_level(value: object, *, default: int) -> int:
    try:
        compact_level = int(value)
    except Exception:
        compact_level = default
    return max(0, min(3, compact_level))


def _normalize_selenium_z_order_mode(value: object, *, default: str) -> str:
    normalized = str(value if value is not None else default).strip().lower()
    return normalized if normalized in {"normal", "behind-main", "bottom"} else default


def build_recording_controller_settings(
    settings_snapshot: Mapping[str, object],
    *,
    default_device: str,
    default_lang_source: str,
    default_lang_target: str,
    default_engine: str,
    default_is_tc: bool,
    default_is_tl: bool,
    normalize_engine_name: NormalizeName,
    normalize_model_key: NormalizeName,
) -> RecordingControllerSettings:
    snapshot = _copy_settings_snapshot(settings_snapshot)
    return RecordingControllerSettings(
        snapshot=snapshot,
        device=str(snapshot.get("input", default_device)),
        lang_source=str(snapshot.get("source_lang_mw", default_lang_source)),
        lang_target=str(snapshot.get("target_lang_mw", default_lang_target)),
        engine=normalize_engine_name(str(snapshot.get("tl_engine_mw", default_engine))),
        model_name_tc=normalize_model_key(str(snapshot.get("model_mw", ""))),
        is_tc=bool(snapshot.get("transcribe_mw", default_is_tc)),
        is_tl=bool(snapshot.get("translate_mw", default_is_tl)),
        selenium_auto_close_on_task_done=bool(snapshot.get("selenium_auto_close_on_task_done", True)),
    )


def build_runtime_model_load_settings(
    settings_snapshot: Mapping[str, object],
    *,
    model_key: str,
    normalize_engine_name: NormalizeName,
) -> RuntimeModelLoadSettings:
    snapshot = _copy_settings_snapshot(settings_snapshot)
    snapshot["model_mw"] = model_key
    snapshot["model_f_import"] = model_key
    engine = normalize_engine_name(str(snapshot.get("tl_engine_mw", "Google Translate")))
    transcribe_enabled = bool(snapshot.get("transcribe_mw", True))
    translate_enabled = bool(snapshot.get("translate_mw", True))
    return RuntimeModelLoadSettings(
        snapshot=snapshot,
        model_key=model_key,
        engine=engine,
        transcribe_enabled=transcribe_enabled,
        translate_enabled=translate_enabled,
        tl_engine_whisper=engine in model_values,
    )


def build_selenium_settings(payload: object) -> SeleniumSettings:
    data = payload if isinstance(payload, dict) else {}
    return SeleniumSettings(
        compact_level=_normalize_compact_level(data.get("compact_level", 2), default=2),
        z_order_mode=_normalize_selenium_z_order_mode(data.get("z_order_mode", "behind-main"), default="behind-main"),
        auto_close_on_task_done=bool(data.get("auto_close_on_task_done", True)),
        chrome_user_data_dir=str(data.get("chrome_user_data_dir", "") or "").strip(),
    )


def normalize_system_setting_value(key: str, value: object) -> object:
    if key == "model_mw":
        return normalize_shared_model_key(value)
    if key == "selenium_compact_level":
        return _normalize_compact_level(value, default=2)
    if key == "selenium_z_order_mode":
        return _normalize_selenium_z_order_mode(value, default="behind-main")
    if key == "selenium_auto_close_on_task_done":
        return bool(value)
    if key == "selenium_chrome_user_data_dir":
        return str(value or "").strip()
    return value


def normalize_import_setting_value(key: str, value: object) -> object:
    if key == "model_f_import":
        return normalize_shared_model_key(value)
    return value


def normalize_record_setting_value(key: str, value: object) -> object:
    if key == "model_device_preference":
        normalized = str(value or "auto").strip().lower()
        return normalized if normalized in {"auto", "cpu", "cuda"} else "auto"
    return value


def build_setting_response(key: str, settings_snapshot: Mapping[str, object]) -> JsonDict:
    return {"key": key, "value": settings_snapshot.get(key)}


def build_compound_setting_response(
    response_key: str,
    settings_snapshot: Mapping[str, object],
    fallback_values: Mapping[str, object],
) -> JsonDict:
    return {
        "key": response_key,
        "value": {
            setting_key: settings_snapshot.get(setting_key, fallback_value)
            for setting_key, fallback_value in fallback_values.items()
        },
    }


__all__ = [
    "RecordingControllerSettings",
    "RuntimeModelLoadSettings",
    "SeleniumSettings",
    "build_compound_setting_response",
    "build_recording_controller_settings",
    "build_runtime_model_load_settings",
    "build_selenium_settings",
    "build_setting_response",
    "normalize_import_setting_value",
    "normalize_record_setting_value",
    "normalize_system_setting_value",
]
