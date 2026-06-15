from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, cast

from speech_translate.utils.types import SettingDict


@dataclass(frozen=True)
class FileRuntimeSettings:
    snapshot: SettingDict
    export_format: str
    export_to: list[object]
    auto_open_dir_export: bool
    auto_open_dir_translate: bool
    auto_open_dir_refinement: bool
    auto_open_dir_alignment: bool
    path_filter_file_import: str
    filter_file_import: bool
    filter_file_import_case_sensitive: bool
    filter_file_import_strip: bool
    filter_file_import_ignore_punctuations: object
    filter_file_import_exact_match: bool
    filter_file_import_similarity: float
    remove_repetition_file_import: bool
    remove_repetition_amount: int
    http_proxy: str
    https_proxy: str
    debug_translate: bool
    libre_link: str
    libre_api_key: str

    def should_auto_open_dir(self, mode: str) -> bool:
        if mode == "refinement":
            return self.auto_open_dir_refinement
        if mode == "alignment":
            return self.auto_open_dir_alignment
        return False


def _copy_settings_snapshot(settings_snapshot: Mapping[str, object]) -> SettingDict:
    return cast(SettingDict, dict(settings_snapshot))


def build_file_runtime_settings(settings_snapshot: Mapping[str, object]) -> FileRuntimeSettings:
    snapshot = _copy_settings_snapshot(settings_snapshot)
    export_to_raw = snapshot.get("export_to", [])
    export_to = list(export_to_raw) if isinstance(export_to_raw, list) else []
    return FileRuntimeSettings(
        snapshot=snapshot,
        export_format=str(snapshot.get("export_format", "%Y-%m-%d %f {file}/{task-lang}")),
        export_to=export_to,
        auto_open_dir_export=bool(snapshot.get("auto_open_dir_export", True)),
        auto_open_dir_translate=bool(snapshot.get("auto_open_dir_translate", True)),
        auto_open_dir_refinement=bool(snapshot.get("auto_open_dir_refinement", True)),
        auto_open_dir_alignment=bool(snapshot.get("auto_open_dir_alignment", True)),
        path_filter_file_import=str(snapshot.get("path_filter_file_import", "")),
        filter_file_import=bool(snapshot.get("filter_file_import", False)),
        filter_file_import_case_sensitive=bool(snapshot.get("filter_file_import_case_sensitive", False)),
        filter_file_import_strip=bool(snapshot.get("filter_file_import_strip", True)),
        filter_file_import_ignore_punctuations=snapshot.get("filter_file_import_ignore_punctuations", "\"',.?!"),
        filter_file_import_exact_match=bool(snapshot.get("filter_file_import_exact_match", True)),
        filter_file_import_similarity=float(snapshot.get("filter_file_import_similarity", 0.75)),
        remove_repetition_file_import=bool(snapshot.get("remove_repetition_file_import", False)),
        remove_repetition_amount=int(snapshot.get("remove_repetition_amount", 1)),
        http_proxy=str(snapshot.get("http_proxy", "")),
        https_proxy=str(snapshot.get("https_proxy", "")),
        debug_translate=bool(snapshot.get("debug_translate", False)),
        libre_link=str(snapshot.get("libre_link", "")),
        libre_api_key=str(snapshot.get("libre_api_key", "")),
    )


__all__ = [
    "FileRuntimeSettings",
    "build_file_runtime_settings",
]
