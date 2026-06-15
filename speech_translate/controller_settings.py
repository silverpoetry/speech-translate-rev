from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, cast

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


def _copy_settings_snapshot(settings_snapshot: Mapping[str, object]) -> SettingDict:
    return cast(SettingDict, dict(settings_snapshot))


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


__all__ = [
    "RecordingControllerSettings",
    "RuntimeModelLoadSettings",
    "build_recording_controller_settings",
    "build_runtime_model_load_settings",
]
