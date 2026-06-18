from __future__ import annotations

import json
from dataclasses import dataclass
from os import makedirs, path
from typing import Dict, Literal, Mapping

from speech_translate._logging import logger
from speech_translate.utils.translate.language import get_whisper_lang_similar

from ..helper import filename_only
from ..whisper.helper import get_task_format


ACTIVE_STATUSES = {"Waiting", "Transcribing please wait...", "Translating please wait...", "Processing", "Re-transcribing..."}
StageKey = Literal["tc", "tl", "mod"]
StatusMap = Dict[int, str]


@dataclass
class FileBatchStatusContext:
    is_tc: bool = False
    is_tl: bool = False
    is_mod: bool = False
    ui_bridge: object | None = None
    tc_status: StatusMap | None = None
    tl_status: StatusMap | None = None
    mod_status: StatusMap | None = None

    def __post_init__(self) -> None:
        if self.tc_status is None:
            self.tc_status = {}
        if self.tl_status is None:
            self.tl_status = {}
        if self.mod_status is None:
            self.mod_status = {}

    def status_map(self, stage: StageKey) -> StatusMap:
        if stage == "tc":
            return self.tc_status
        if stage == "tl":
            return self.tl_status
        return self.mod_status

    def combined_status(self, index: int) -> str:
        return _build_combined_status(
            index,
            is_tc=self.is_tc,
            is_tl=self.is_tl,
            is_mod=self.is_mod,
            tc_status=self.tc_status,
            tl_status=self.tl_status,
            mod_status=self.mod_status,
        )

    def is_completed(self, index: int, combined_status: str | None = None) -> bool:
        combined_status = self.combined_status(index) if combined_status is None else combined_status
        return _is_file_status_completed(
            index,
            combined_status,
            is_tc=self.is_tc,
            is_tl=self.is_tl,
            is_mod=self.is_mod,
            tc_status=self.tc_status,
            tl_status=self.tl_status,
            mod_status=self.mod_status,
        )

    def is_active(self, index: int) -> bool:
        return any(
            enabled and self.status_map(stage).get(index, "Waiting") in ACTIVE_STATUSES
            for stage, enabled in (("tc", self.is_tc), ("tl", self.is_tl), ("mod", self.is_mod))
        )

    def has_active_work(self, item_count: int) -> bool:
        return any(self.is_active(index) for index in range(item_count))

    def sync_ui(self, index: int) -> None:
        combined_status = self.combined_status(index)
        if self.ui_bridge is None:
            return
        self.ui_bridge.sync_file_status(index, combined_status, self.is_completed(index, combined_status))

    def update_status(self, stage: StageKey, index: int, msg: str) -> None:
        self.status_map(stage)[index] = msg
        self.sync_ui(index)


@dataclass(frozen=True)
class FileExportPlan:
    export_dir: str
    base_name: str
    save_name: str
    metadata_path: str

    @property
    def save_base_path(self) -> str:
        return path.join(self.export_dir, self.save_name)


def _build_combined_status(
    index: int,
    *,
    is_tc: bool,
    is_tl: bool,
    is_mod: bool,
    tc_status: Mapping[int, str],
    tl_status: Mapping[int, str],
    mod_status: Mapping[int, str],
) -> str:
    parts: list[str] = []
    if is_tc:
        current = tc_status.get(index, "Waiting")
        if current and current != "Waiting":
            parts.append(current)
    if is_tl:
        current = tl_status.get(index, "Waiting")
        if current and current != "Waiting":
            parts.append(current)
    if is_mod:
        current = mod_status.get(index, "Waiting")
        if current and current != "Waiting":
            parts.append(current)
    return ", ".join(parts) if parts else "Waiting"


def _is_file_status_completed(
    index: int,
    combined_status: str,
    *,
    is_tc: bool,
    is_tl: bool,
    is_mod: bool,
    tc_status: Mapping[int, str],
    tl_status: Mapping[int, str],
    mod_status: Mapping[int, str],
) -> bool:
    lower_status = combined_status.lower()
    if "fail" in lower_status or "error" in lower_status or "parse error" in lower_status:
        return True
    if is_tc and is_tl:
        return "transcribed" in tc_status.get(index, "").lower() and "translated" in tl_status.get(index, "").lower()
    if is_tc:
        return "transcribed" in tc_status.get(index, "").lower()
    if is_tl:
        return "translated" in tl_status.get(index, "").lower()
    if is_mod:
        mod_value = mod_status.get(index, "").lower()
        return "refined" in mod_value or "aligned" in mod_value or "translated" in mod_value
    return False


def _update_status(status_context: FileBatchStatusContext, stage: StageKey, index: int, msg: str) -> None:
    status_context.update_status(stage, index, msg)


def _save_metadata(filepath: str, meta_data: dict) -> None:
    try:
        makedirs(path.dirname(filepath), exist_ok=True)
        if path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as file_handle:
                existing = json.load(file_handle)
                existing.update(meta_data)
                meta_data = existing
        with open(filepath, "w", encoding="utf-8") as file_handle:
            json.dump(meta_data, file_handle, ensure_ascii=False, indent=4)
    except Exception as exc:
        logger.warning(f"Failed to save metadata: {exc}")


def _build_base_export_name(template: str, file_name: str, lang_src: str, lang_tgt: str, tc_model: str, tl_engine: str) -> str:
    return (
        template.replace("{file}", file_name)
        .replace("{lang-source}", lang_src)
        .replace("{lang-target}", lang_tgt)
        .replace("{transcribe-with}", tc_model)
        .replace("{translate-with}", tl_engine)
    )


def _build_metadata_name(base_name: str) -> str:
    meta_name = base_name
    for fmt, val in get_task_format("metadata", "metadata", "metadata", "metadata", both=True).items():
        meta_name = meta_name.replace(fmt, val)
    return meta_name


def _apply_task_format(base_name: str, format_dict: Mapping[str, str]) -> str:
    save_name = base_name
    for fmt, val in format_dict.items():
        save_name = save_name.replace(fmt, val)
    return save_name


def _build_export_plan(export_dir: str, base_name: str, format_dict: Mapping[str, str]) -> FileExportPlan:
    save_name = _apply_task_format(base_name, format_dict)
    metadata_name = _build_metadata_name(base_name)
    return FileExportPlan(
        export_dir=export_dir,
        base_name=base_name,
        save_name=save_name,
        metadata_path=path.join(export_dir, metadata_name + ".json"),
    )


def _save_export_plan_metadata(export_plan: FileExportPlan, meta_data: Mapping[str, object]) -> None:
    _save_metadata(export_plan.metadata_path, dict(meta_data))


def _resolve_slice_bounds(setting_cache: Mapping[str, object]) -> tuple[int | None, int | None]:
    slice_start = int(setting_cache["file_slice_start"]) if setting_cache["file_slice_start"] else None
    slice_end = int(setting_cache["file_slice_end"]) if setting_cache["file_slice_end"] else None
    return slice_start, slice_end


def _slice_display_name(file_path: str, *, start: int | None, end: int | None) -> str:
    return filename_only(file_path)[start:end]


def _resolve_alignment_language(file_data: list[object]) -> str | None:
    if len(file_data) <= 2:
        return None
    candidate = str(file_data[2] or "")
    if len(candidate) <= 3:
        return None
    return get_whisper_lang_similar(candidate)


__all__ = [
    "ACTIVE_STATUSES",
    "FileBatchStatusContext",
    "FileExportPlan",
    "StageKey",
    "StatusMap",
    "_apply_task_format",
    "_build_base_export_name",
    "_build_combined_status",
    "_build_export_plan",
    "_build_metadata_name",
    "_is_file_status_completed",
    "_resolve_alignment_language",
    "_resolve_slice_bounds",
    "_save_export_plan_metadata",
    "_slice_display_name",
    "_update_status",
]
