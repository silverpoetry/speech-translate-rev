from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, cast

from speech_translate.utils.types import SettingDict


@dataclass(frozen=True)
class RealtimeTranslationSettings:
    snapshot: SettingDict
    http_proxy: str
    https_proxy: str
    libre_link: str
    libre_api_key: str
    filter_rec: bool
    filter_rec_case_sensitive: bool
    filter_rec_strip: bool
    filter_rec_ignore_punctuations: object
    filter_rec_exact_match: bool
    filter_rec_similarity: float


def _copy_settings_snapshot(settings_snapshot: Mapping[str, object]) -> SettingDict:
    return cast(SettingDict, dict(settings_snapshot))


def build_realtime_translation_settings(settings_snapshot: Mapping[str, object]) -> RealtimeTranslationSettings:
    snapshot = _copy_settings_snapshot(settings_snapshot)
    return RealtimeTranslationSettings(
        snapshot=snapshot,
        http_proxy=str(snapshot.get("http_proxy", "")),
        https_proxy=str(snapshot.get("https_proxy", "")),
        libre_link=str(snapshot.get("libre_link", "")),
        libre_api_key=str(snapshot.get("libre_api_key", "")),
        filter_rec=bool(snapshot.get("filter_rec", False)),
        filter_rec_case_sensitive=bool(snapshot.get("filter_rec_case_sensitive", False)),
        filter_rec_strip=bool(snapshot.get("filter_rec_strip", True)),
        filter_rec_ignore_punctuations=snapshot.get("filter_rec_ignore_punctuations", "\"',.?!"),
        filter_rec_exact_match=bool(snapshot.get("filter_rec_exact_match", False)),
        filter_rec_similarity=float(snapshot.get("filter_rec_similarity", 0.75)),
    )


__all__ = [
    "RealtimeTranslationSettings",
    "build_realtime_translation_settings",
]
