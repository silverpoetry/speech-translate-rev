from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, cast

from speech_translate.utils.types import SettingDict


@dataclass(frozen=True)
class WhisperSaveRuntimeSettings:
    snapshot: SettingDict
    whisper_args: str
    segment_level: bool
    word_level: bool


def _copy_settings_snapshot(settings_snapshot: Mapping[str, object]) -> SettingDict:
    return cast(SettingDict, dict(settings_snapshot))


def build_whisper_save_runtime_settings(settings_snapshot: Mapping[str, object]) -> WhisperSaveRuntimeSettings:
    snapshot = _copy_settings_snapshot(settings_snapshot)
    return WhisperSaveRuntimeSettings(
        snapshot=snapshot,
        whisper_args=str(snapshot.get("whisper_args", "")),
        segment_level=bool(snapshot.get("segment_level", True)),
        word_level=bool(snapshot.get("word_level", True)),
    )


__all__ = [
    "WhisperSaveRuntimeSettings",
    "build_whisper_save_runtime_settings",
]
