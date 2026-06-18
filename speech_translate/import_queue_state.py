from __future__ import annotations

import os
from dataclasses import dataclass
from threading import RLock

from speech_translate.controller_protocols import JsonDict


@dataclass
class QueueItem:
    path: str
    name: str
    status: str = ""
    is_completed: bool = False

    def to_dict(self) -> JsonDict:
        return {
            "path": self.path,
            "name": self.name,
            "status": self.status,
            "is_completed": self.is_completed,
        }


class ImportQueueStateStore:
    """Owns mutable import queue state and derived display projections."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.file_import_queue: list[object] = []
        self.processing_queue: list[JsonDict] = []

    def get_display_queue(self) -> list[JsonDict]:
        with self._lock:
            display_list = [self.normalize_queue_item(entry).to_dict() for entry in self.file_import_queue]
            if self.processing_queue:
                processing_map = {item.get("path"): item for item in self.processing_queue if item.get("path")}
                for item in display_list:
                    path = item.get("path")
                    if path in processing_map:
                        processing_item = processing_map[path]
                        item["status"] = str(processing_item.get("status", item.get("status", "")))
                        item["is_completed"] = bool(processing_item.get("is_completed", item.get("is_completed", False)))
            return display_list

    def set_processing_batch(self, files: list[object]) -> None:
        with self._lock:
            self.processing_queue = [self.make_queue_item(str(file_path), status="Waiting").to_dict() for file_path in files]

    def sync_processing_status(self, index: int, combined_status: str, is_completed: bool) -> None:
        with self._lock:
            if self.processing_queue and 0 <= index < len(self.processing_queue):
                if not self.processing_queue[index].get("is_completed", False) or is_completed:
                    self.processing_queue[index]["status"] = combined_status
                    self.processing_queue[index]["is_completed"] = is_completed

    def add_files(self, files: list[str]) -> int:
        added = 0
        with self._lock:
            for file_path in files:
                normalized_path = str(file_path)
                if not any(self.normalize_queue_item(queue_item).path == normalized_path for queue_item in self.file_import_queue):
                    self.file_import_queue.append(self.make_queue_item(normalized_path, status="Waiting").to_dict())
                    added += 1
        return added

    def remove_by_index(self, index: int) -> object | None:
        with self._lock:
            if self.processing_queue and 0 <= index < len(self.processing_queue):
                removed = self.processing_queue.pop(index)
                path_to_remove = removed.get("path")
                for queue_index, queue_item in enumerate(list(self.file_import_queue)):
                    if self.normalize_queue_item(queue_item).path == path_to_remove:
                        self.file_import_queue.pop(queue_index)
                        break
                return removed

            if index < 0 or index >= len(self.file_import_queue):
                return None
            return self.file_import_queue.pop(index)

    def clear(self) -> None:
        with self._lock:
            self.file_import_queue = []
            self.processing_queue = []

    def extract_files_to_process(self) -> list[str]:
        with self._lock:
            return [
                normalized.path
                for normalized in (self.normalize_queue_item(entry) for entry in self.file_import_queue)
                if not normalized.is_completed and normalized.path
            ]

    def finalize_processing_queue(self) -> None:
        with self._lock:
            processing_map = {item.get("path"): item for item in self.processing_queue}
            for index, entry in enumerate(self.file_import_queue):
                path = self.normalize_queue_item(entry).path
                if path in processing_map:
                    processing_item = processing_map[path]
                    self.file_import_queue[index] = QueueItem(
                        path=path,
                        name=processing_item.get("name", os.path.basename(path)),
                        status=processing_item.get("status", ""),
                        is_completed=bool(processing_item.get("is_completed", False)),
                    ).to_dict()
            self.processing_queue = []

    def cancel_processing(self, *, status: str = "Cancelled") -> bool:
        with self._lock:
            if not self.processing_queue:
                return False
            for item in self.processing_queue:
                item["status"] = status
            return True

    @staticmethod
    def make_queue_item(file_path: str, *, status: str = "", is_completed: bool = False) -> QueueItem:
        normalized_path = str(file_path)
        return QueueItem(
            path=normalized_path,
            name=os.path.basename(normalized_path),
            status=status,
            is_completed=is_completed,
        )

    @staticmethod
    def normalize_queue_item(entry: object) -> QueueItem:
        if isinstance(entry, QueueItem):
            return entry
        if isinstance(entry, dict):
            path = str(entry.get("path", ""))
            return QueueItem(
                path=path,
                name=str(entry.get("name", os.path.basename(path))),
                status=str(entry.get("status", "")),
                is_completed=bool(entry.get("is_completed", False)),
            )
        text = str(entry)
        return QueueItem(path=text, name=os.path.basename(text))


__all__ = [
    "ImportQueueStateStore",
    "QueueItem",
]
