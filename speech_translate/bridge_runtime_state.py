from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock, Thread
from typing import List, Optional


@dataclass
class BridgeVisualRuntime:
    cuda: str = ""
    running_after_id: str = ""
    bg_color: str = ""
    fg_color: str = ""
    has_ffmpeg: bool = False
    web_bridge: object | None = None


@dataclass
class BridgeFileRuntime:
    file_processing: bool = False
    transcribing_file: bool = False
    translating_file: bool = False
    file_tced_counter: int = 0
    file_tled_counter: int = 0
    mod_file_counter: int = 0


@dataclass
class BridgeDownloadRuntime:
    dl_thread: Optional[Thread] = None
    cancel_dl: bool = False


@dataclass
class BridgeRecordingRuntime:
    rec_tc_thread: Optional[Thread] = None
    rec_tl_thread: Optional[Thread] = None
    recording: bool = False
    stream: Optional[object] = None
    data_queue: object | None = None
    current_rec_status: str = ""
    tc_lock: Optional[Lock] = None


@dataclass
class BridgeLiveTextRuntime:
    auto_detected_lang: str = "~"
    tc_sentences: List = field(default_factory=list)
    tl_sentences: List = field(default_factory=list)


__all__ = [
    "BridgeVisualRuntime",
    "BridgeFileRuntime",
    "BridgeDownloadRuntime",
    "BridgeRecordingRuntime",
    "BridgeLiveTextRuntime",
]
