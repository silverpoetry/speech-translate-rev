from __future__ import annotations

import os
from dataclasses import dataclass
from threading import Thread
from time import sleep, time
from typing import Callable


def _noop(*_args, **_kwargs) -> None:
    return


@dataclass(frozen=True)
class TaskReporter:
    reset_task_state: Callable[[str], None] = _noop
    update_task_message: Callable[[str], None] = _noop
    update_task_progress: Callable[[float], None] = _noop
    finish_task: Callable[[str], None] = _noop
    update_task_error: Callable[[str], None] = _noop


@dataclass(frozen=True)
class DownloadProgressSnapshot:
    current_bytes: int
    total_bytes: int
    progress: float
    speed_bytes_per_sec: float
    speed_text: str
    size_text: str
    elapsed_seconds: float


@dataclass(frozen=True)
class DownloadMonitorResult:
    cancelled: bool
    error: Exception | None


def start_optional_callback(callback) -> None:
    if callback is None:
        return
    Thread(target=callback, daemon=True).start()


def path_size(path: str) -> int:
    if not path:
        return 0
    if os.path.isfile(path):
        return os.path.getsize(path)
    if os.path.isdir(path):
        return sum(os.path.getsize(os.path.join(root, file_name)) for root, _, files in os.walk(path) for file_name in files)
    return 0


def format_bytes(value: float) -> str:
    if value <= 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024.0:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    return f"{value:.1f} PB"


def build_download_progress_snapshot(
    *,
    current_bytes: int,
    total_bytes: int,
    started_at: float,
    previous_bytes: int,
    previous_time: float,
    current_time: float | None = None,
    progress_floor: float = 0.0,
    progress_ceiling: float = 100.0,
    allow_time_fallback: bool = True,
) -> DownloadProgressSnapshot:
    now = time() if current_time is None else current_time
    elapsed_seconds = max(0.0, now - started_at)
    delta_time = max(0.2, now - previous_time)
    speed_bytes_per_sec = max(0.0, current_bytes - previous_bytes) / delta_time
    speed_text = f"{format_bytes(speed_bytes_per_sec)}/s" if speed_bytes_per_sec > 0 else "-"

    if total_bytes > 0:
        progress = (current_bytes / total_bytes) * progress_ceiling
        progress = max(progress_floor, min(progress_ceiling, progress))
        size_text = f"{format_bytes(current_bytes)}/{format_bytes(total_bytes)}"
    else:
        progress = min(progress_ceiling, max(progress_floor, progress_floor + elapsed_seconds * 0.9)) if allow_time_fallback else progress_floor
        size_text = format_bytes(current_bytes)

    return DownloadProgressSnapshot(
        current_bytes=current_bytes,
        total_bytes=total_bytes,
        progress=float(progress),
        speed_bytes_per_sec=float(speed_bytes_per_sec),
        speed_text=speed_text,
        size_text=size_text,
        elapsed_seconds=elapsed_seconds,
    )


def monitor_threaded_download(
    *,
    download_fn: Callable[[], None],
    observe_path: str,
    total_bytes: int,
    on_progress: Callable[[DownloadProgressSnapshot], None] | None = None,
    poll_interval: float = 0.1,
    cancel_requested: Callable[[], bool] | None = None,
    cancel_handler: Callable[[Thread], None] | None = None,
    progress_floor: float = 0.0,
    progress_ceiling: float = 100.0,
    initial_progress_callback: Callable[[DownloadProgressSnapshot], None] | None = None,
) -> DownloadMonitorResult:
    result_box: dict[str, Exception | None] = {"error": None}

    def run_threaded() -> None:
        try:
            download_fn()
        except Exception as exc:
            result_box["error"] = exc

    worker = Thread(target=run_threaded, daemon=True)
    worker.start()

    started_at = time()
    previous_bytes = 0
    previous_time = started_at

    if initial_progress_callback is not None:
        initial_progress_callback(
            build_download_progress_snapshot(
                current_bytes=0,
                total_bytes=total_bytes,
                started_at=started_at,
                previous_bytes=0,
                previous_time=started_at,
                current_time=started_at,
                progress_floor=progress_floor,
                progress_ceiling=progress_ceiling,
                allow_time_fallback=True,
            )
        )

    while worker.is_alive():
        if cancel_requested is not None and cancel_requested():
            if cancel_handler is not None:
                cancel_handler(worker)
            return DownloadMonitorResult(cancelled=True, error=None)

        sleep(poll_interval)
        current_time = time()
        current_bytes = path_size(observe_path)
        if on_progress is not None:
            snapshot = build_download_progress_snapshot(
                current_bytes=current_bytes,
                total_bytes=total_bytes,
                started_at=started_at,
                previous_bytes=previous_bytes,
                previous_time=previous_time,
                current_time=current_time,
                progress_floor=progress_floor,
                progress_ceiling=progress_ceiling,
                allow_time_fallback=True,
            )
            on_progress(snapshot)
        previous_bytes = current_bytes
        previous_time = current_time

    worker.join()
    return DownloadMonitorResult(cancelled=False, error=result_box["error"])
