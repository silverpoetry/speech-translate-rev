from __future__ import annotations

from dataclasses import dataclass
from threading import Thread
from time import sleep
from typing import Callable, Mapping, Protocol

from speech_translate.utils.audio.file_runtime_settings import build_file_runtime_settings

from ..helper import get_proxies, kill_thread
from ..translate.translator import translate


class ResultQueueLike(Protocol):
    def get(self): ...
    def put(self, payload) -> None: ...


class FileEnvironmentLike(Protocol):
    has_ffmpeg: bool


class FileSettingsLike(Protocol):
    cache: Mapping[str, object]


@dataclass
class WorkerFailure:
    failed: bool = False
    error: Exception | None = None

    def capture(self, exc: Exception) -> None:
        self.failed = True
        self.error = exc

    def raise_if_failed(self) -> None:
        if self.failed:
            raise self.error or RuntimeError("Unknown worker failure")


def monitor_thread(
    thread: Thread,
    check_cancel: Callable[[], bool],
    *,
    cancel_thread: Callable[[Thread], None] = kill_thread,
    sleep_fn: Callable[[float], None] = sleep,
) -> None:
    while thread.is_alive():
        if not check_cancel():
            cancel_thread(thread)
            raise Exception("Cancelled")
        sleep_fn(0.1)


def run_monitored_worker(
    target: Callable,
    *,
    cancel_check: Callable[[], bool],
    args: tuple = (),
    kwargs: Mapping[str, object] | None = None,
    thread_factory: Callable[..., Thread] = Thread,
    monitor_thread_fn: Callable[[Thread, Callable[[], bool]], None] = monitor_thread,
) -> None:
    thread = thread_factory(target=target, args=args, kwargs=dict(kwargs or {}), daemon=True)
    thread.start()
    monitor_thread_fn(thread, cancel_check)


def execute_monitored_queue_task(
    target: Callable,
    *,
    cancel_check: Callable[[], bool],
    args: tuple = (),
    kwargs: Mapping[str, object] | None = None,
    fail_status: WorkerFailure | None = None,
    raise_failure: bool = True,
    result_queue: ResultQueueLike,
    run_worker_fn: Callable[..., None] = run_monitored_worker,
):
    run_worker_fn(target, cancel_check=cancel_check, args=args, kwargs=kwargs)
    if fail_status is not None:
        if raise_failure:
            fail_status.raise_if_failed()
        elif fail_status.failed:
            return None
    return result_queue.get()


def run_whisper(
    func,
    audio: str | None,
    task: str,
    fail_status: WorkerFailure,
    *,
    result_queue: ResultQueueLike,
    environment: FileEnvironmentLike,
    **kwargs,
) -> None:
    try:
        result = func(audio, task=task, **kwargs)
        result_queue.put(result)
    except Exception as exc:
        fail_status.capture(exc)
        if "The system cannot find the file specified" in str(exc) and not environment.has_ffmpeg:
            fail_status.error = Exception("FFmpeg not found in system path. Please install FFmpeg.")


def _apply_translation_result_to_segments(query, result) -> None:
    for segment in query.segments:
        if not result:
            return
        if isinstance(result, str):
            raise Exception(result)

        translated_text = " " + str(result.pop(0))
        temp_words = translated_text.split()
        segment_words = [word for word in getattr(segment, "words", []) if hasattr(word, "word")]

        if len(temp_words) == len(segment_words):
            for word in segment_words:
                word.word = " " + temp_words.pop(0)
        elif not segment_words:
            setattr(segment, "_default_text", translated_text)
        else:
            if len(temp_words) > len(segment_words):
                for idx, word in enumerate(temp_words):
                    target_idx = min(idx, len(segment_words) - 1)
                    if idx < len(segment_words):
                        segment_words[target_idx].word = " " + word
                    else:
                        segment_words[target_idx].word += f" {word}"
            else:
                last_end = segment_words[-1].end
                for idx, word in enumerate(temp_words):
                    segment_words[idx].word = " " + word
                segment.words = segment_words[:len(temp_words)]
                segment.words[-1].end = last_end


def run_translate_api(
    query,
    engine: str,
    lang_source: str,
    lang_target: str,
    fail_status: WorkerFailure,
    settings: FileSettingsLike,
    *,
    translate_fn: Callable[..., tuple[bool, object]] = translate,
    runtime_settings_factory: Callable[[Mapping[str, object]], object] = build_file_runtime_settings,
    proxies_getter: Callable[[str, str], dict[str, str]] = get_proxies,
    **kwargs,
) -> None:
    try:
        segment_texts = [segment.text for segment in query.segments]
        query.language = lang_target
        runtime_settings = runtime_settings_factory(settings.cache)
        _success, result = translate_fn(
            engine,
            segment_texts,
            lang_source,
            lang_target,
            proxies_getter(runtime_settings.http_proxy, runtime_settings.https_proxy),
            runtime_settings.debug_translate,
            **kwargs,
        )
        _apply_translation_result_to_segments(query, result)
    except Exception as exc:
        fail_status.capture(exc)


__all__ = [
    "WorkerFailure",
    "execute_monitored_queue_task",
    "monitor_thread",
    "run_monitored_worker",
    "run_translate_api",
    "run_whisper",
]
