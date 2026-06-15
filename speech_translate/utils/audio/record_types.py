from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Callable, Literal, Protocol

import numpy as np

from speech_translate._constants import MAX_THRESHOLD, MIN_THRESHOLD


class ResultLike(Protocol):
    text: str


class SegmentLike(Protocol):
    def to_dict(self) -> dict[str, object]:
        ...


class TranscriptionResultLike(ResultLike, Protocol):
    language: str
    segments: list[SegmentLike]


class LockLike(Protocol):
    def __enter__(self) -> object:
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        ...


class WhisperCallable(Protocol):
    def __call__(self, audio: "AudioTarget", *, task: str, **kwargs: object) -> TranscriptionResultLike:
        ...


class SileroVadLike(Protocol):
    def __call__(self, audio, sample_rate: int):
        ...

    def reset_states(self) -> None:
        ...


ResultSnapshot = ResultLike | str
AudioTarget = str | np.ndarray | Any
TranslationApiResult = str | list[str]
HallucinationFilters = dict[str, object]


@dataclass
class RealtimeSharedState:
    prev_tc_res: ResultSnapshot = ""
    prev_tl_res: ResultSnapshot = ""
    last_db: float | None = None


@dataclass
class TranslationTask:
    kind: Literal["whisper", "api"]
    separator: str
    audio: AudioTarget | None = None
    cleanup_audio: bool = False
    text: str = ""
    lang_source: str = ""
    lang_target: str = ""
    engine: str = ""


@dataclass
class RecordingRuntime:
    taskname: str
    device: str
    lang_source: str
    lang_target: str
    engine: str
    is_tl: bool
    use_temp: bool
    separator: str
    keep_temp: bool
    t_start: float
    max_buffer_s: float
    max_sentences: int
    sentence_limitless: bool
    lang_target_display: str


@dataclass
class RecordingSessionConfig:
    rec_type: str
    transcribe_rate: timedelta
    max_buffer_s: int
    max_sentences: int
    sentence_limitless: bool
    min_input_length: float
    keep_temp: bool
    tl_engine_whisper: bool
    taskname: str
    auto: bool
    threshold_enable: bool
    threshold_db: float
    threshold_auto: bool
    use_silero: bool
    silero_min_conf: float
    auto_break_buffer: bool
    use_temp: bool
    separator: str


@dataclass
class RecordingModelRuntime:
    stable_tc: WhisperCallable | None
    stable_tl: WhisperCallable | None
    whisper_args: dict[str, object]
    configured_whisper_language: str | None
    demucs_enabled: bool
    hallucination_filters: HallucinationFilters
    cuda_device: str
    use_temp: bool


@dataclass
class RecordingStreamRuntime:
    input_device_index: int
    sr_ori: int
    num_of_channels: int
    chunk_size: int
    samp_width: int
    sr_divider: int
    callback_ctx: "RealtimeCallbackContext"


@dataclass
class RecordingSessionServices:
    runtime: RecordingRuntime
    status_emitter: "RecordingStatusEmitter"
    translator: "TranslationDispatcher"
    buffer_reducer: "BufferStateReducer"
    control: object | None = None
    status_getter: Callable[[], str] | None = None

    def update_status(self) -> None:
        if self.status_getter is not None:
            self.status_emitter.emit(status=self.status_getter())
            return
        if self.control is not None and hasattr(self.control, "current_status"):
            self.status_emitter.emit(status=self.control.current_status())
            return
        raise RuntimeError("RecordingSessionServices.status_getter is required")


@dataclass
class RecordingSessionLifecycle:
    session_state: "RealtimeSessionState"
    services: RecordingSessionServices
    callback_ctx: "RealtimeCallbackContext"
    sr_ori: int
    num_of_channels: int
    samp_width: int
    sr_divider: int


@dataclass
class RecordingSessionFinalizeContext:
    session_state: "RealtimeSessionState" | None = None
    update_status: Callable[[], None] | None = None
    keep_temp: bool = True

    @classmethod
    def from_lifecycle(cls, lifecycle: RecordingSessionLifecycle | None) -> "RecordingSessionFinalizeContext":
        if lifecycle is None:
            return cls()

        return cls(
            session_state=lifecycle.session_state,
            update_status=lifecycle.services.update_status,
            keep_temp=lifecycle.services.runtime.keep_temp,
        )


@dataclass
class RecordingSessionBootstrap:
    config: RecordingSessionConfig
    model_runtime: RecordingModelRuntime
    stream_runtime: RecordingStreamRuntime


@dataclass
class SmartSplitOutcome:
    pre_audio_bytes: bytes
    post_audio_bytes: bytes
    pre_result: TranscriptionResultLike
    post_result: TranscriptionResultLike


@dataclass
class RealtimeSessionState:
    last_sample: bytes = b""
    duration_seconds: float = 0.0
    prev_tc_buffer_seconds: float = 0.0
    next_transcribe_time: datetime | None = None
    paused: bool = False
    temp_audio_paths: list[str] = field(default_factory=list)
    transcription_lock: LockLike | None = None

    def append_audio(self, audio_bytes: bytes) -> None:
        self.last_sample += audio_bytes

    def recalculate_duration(self, *, samp_width: int, num_of_channels: int, sr_divider: int) -> float:
        if samp_width <= 0 or num_of_channels <= 0 or sr_divider <= 0:
            self.duration_seconds = 0.0
            return self.duration_seconds
        self.duration_seconds = len(self.last_sample) / (samp_width * num_of_channels * sr_divider)
        return self.duration_seconds

    def reset_buffer(self) -> None:
        self.last_sample = b""
        self.duration_seconds = 0.0
        self.prev_tc_buffer_seconds = 0.0


@dataclass
class RealtimeCallbackContext:
    sample_rate: int
    frame_duration_ms: int
    threshold_enable: bool
    threshold_db: float
    threshold_auto: bool
    use_silero: bool
    silero_min_conf: float
    vad_checked: bool
    num_of_channels: int
    samp_width: int
    use_temp: bool
    shared_runtime_state: RealtimeSharedState | None = None
    max_db: float = MAX_THRESHOLD
    min_db: float = MIN_THRESHOLD
    is_silence: bool = False
    was_recording: bool = False
    silence_started_at: float = 0.0
    silero_disabled: bool = False
    webrtc_vad: Any | None = None
    silero_vad: SileroVadLike | None = None
