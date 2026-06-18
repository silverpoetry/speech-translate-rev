from __future__ import annotations

from ast import literal_eval
from dataclasses import dataclass
from datetime import timedelta
from shlex import quote
from typing import Mapping, cast

from speech_translate.utils.audio.device import AudioDeviceSettings
from speech_translate.utils.audio.record_types import RecordingSessionConfig
from speech_translate.utils.types import SettingDict
from speech_translate.utils.whisper.helper import model_values

from ..helper import str_separator_to_html


@dataclass(frozen=True)
class RecordingModelSettings:
    snapshot: SettingDict
    enable_initial_prompt: bool
    initial_prompts_map: Mapping[str, object]
    use_faster_whisper: bool
    filter_rec: bool
    path_filter_rec: str


@dataclass(frozen=True)
class RecordingStreamSettings:
    snapshot: SettingDict
    device_settings: AudioDeviceSettings
    threshold_auto_mode: int
    suppress_record_warning: bool


def _copy_settings_snapshot(settings_snapshot: Mapping[str, object]) -> SettingDict:
    return cast(SettingDict, dict(settings_snapshot))


def build_recording_session_config(
    *,
    rec_type: str,
    lang_source: str,
    engine: str,
    is_tc: bool,
    is_tl: bool,
    settings_snapshot: Mapping[str, object],
) -> RecordingSessionConfig:
    snapshot = _copy_settings_snapshot(settings_snapshot)
    return RecordingSessionConfig(
        rec_type=rec_type,
        transcribe_rate=timedelta(seconds=snapshot["transcribe_rate"] / 1000),
        max_buffer_s=int(snapshot.get(f"max_buffer_{rec_type}", 10)),
        max_sentences=int(snapshot.get(f"max_sentences_{rec_type}", 5)),
        sentence_limitless=bool(snapshot.get(f"{rec_type}_no_limit", False)),
        min_input_length=float(snapshot.get(f"min_input_length_{rec_type}", 0.4)),
        keep_temp=bool(snapshot.get("keep_temp", False)),
        tl_engine_whisper=engine in model_values,
        taskname="Transcribe & Translate" if is_tc and is_tl else "Transcribe" if is_tc else "Translate",
        auto=lang_source.lower() == "auto detect",
        threshold_enable=bool(snapshot.get(f"threshold_enable_{rec_type}", True)),
        threshold_db=float(snapshot.get(f"threshold_db_{rec_type}", -20)),
        threshold_auto=bool(snapshot.get(f"threshold_auto_{rec_type}", True)),
        use_silero=bool(snapshot.get(f"threshold_auto_silero_{rec_type}", True)),
        silero_min_conf=float(snapshot.get(f"threshold_silero_{rec_type}_min", 0.75)),
        auto_break_buffer=bool(snapshot.get(f"auto_break_buffer_{rec_type}", True)),
        use_temp=bool(snapshot["use_temp"]),
        separator=str_separator_to_html(literal_eval(quote(snapshot["separate_with"]))),
    )


def build_recording_model_settings(settings_snapshot: Mapping[str, object]) -> RecordingModelSettings:
    snapshot = _copy_settings_snapshot(settings_snapshot)
    return RecordingModelSettings(
        snapshot=snapshot,
        enable_initial_prompt=bool(snapshot.get("enable_initial_prompt", False)),
        initial_prompts_map=cast(Mapping[str, object], snapshot.get("initial_prompts_map", {})),
        use_faster_whisper=bool(snapshot.get("use_faster_whisper", False)),
        filter_rec=bool(snapshot.get("filter_rec", False)),
        path_filter_rec=str(snapshot.get("path_filter_rec", "")),
    )


def build_recording_stream_settings(
    *,
    rec_type: str,
    settings_snapshot: Mapping[str, object],
) -> RecordingStreamSettings:
    snapshot = _copy_settings_snapshot(settings_snapshot)
    threshold_auto_mode_raw = snapshot.get(f"threshold_auto_level_{rec_type}", 3)
    try:
        threshold_auto_mode = int(threshold_auto_mode_raw)
    except Exception:
        threshold_auto_mode = 3
    return RecordingStreamSettings(
        snapshot=snapshot,
        device_settings=AudioDeviceSettings(cache=snapshot),
        threshold_auto_mode=threshold_auto_mode,
        suppress_record_warning=bool(snapshot.get("supress_record_warning", False)),
    )


__all__ = [
    "RecordingModelSettings",
    "RecordingStreamSettings",
    "build_recording_model_settings",
    "build_recording_session_config",
    "build_recording_stream_settings",
]
