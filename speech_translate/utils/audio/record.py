import os
import re
from ast import literal_eval
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from io import BytesIO
from platform import system
from queue import Empty, Queue
from shlex import quote
from threading import Lock, Thread
from time import gmtime, sleep, strftime, time
from types import TracebackType
from wave import open as w_open

import numpy as np
import torch
import torchaudio
import webrtcvad
from typing import Literal, Protocol, cast
from whisper.tokenizer import TO_LANGUAGE_CODE

from speech_translate._constants import MAX_THRESHOLD, MIN_THRESHOLD, WHISPER_SR
from speech_translate._logging import logger
from speech_translate._path import dir_silero_vad, dir_temp
from speech_translate.linker import bc, sj
from speech_translate.utils.audio.audio import get_db, get_frame_duration, get_speech_webrtc, resample_sr, to_silero
from speech_translate.utils.audio.device import get_device_details
from speech_translate.utils.translate.language import get_whisper_lang_name, get_whisper_lang_similar, verify_language_in_key

from ..helper import generate_temp_filename, get_proxies, str_separator_to_html, unique_rec_list
from ..translate.translator import translate
from ..whisper.helper import get_hallucination_filter, model_values
from ..whisper.load import get_model, get_model_args, get_tc_args
from ..whisper.result import remove_segments_by_str

if system() == "Windows":
    import pyaudiowpatch as pyaudio
else:
    import pyaudio


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
AudioTarget = str | np.ndarray | torch.Tensor
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
class RealtimeSessionState:
    last_sample: bytes = b""
    duration_seconds: float = 0.0
    prev_tc_buffer_seconds: float = 0.0
    next_transcribe_time: datetime | None = None
    paused: bool = False
    temp_audio_paths: list[str] = field(default_factory=list)

    def append_audio(self, audio_bytes: bytes) -> None:
        self.last_sample += audio_bytes

    def recalculate_duration(self, *, samp_width: int, num_of_channels: int, sr_divider: int) -> float:
        self.duration_seconds = _calculate_buffer_duration(
            self.last_sample,
            samp_width=samp_width,
            num_of_channels=num_of_channels,
            sr_divider=sr_divider,
        )
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
    max_db: float = MAX_THRESHOLD
    min_db: float = MIN_THRESHOLD
    is_silence: bool = False
    was_recording: bool = False
    silence_started_at: float = 0.0
    silero_disabled: bool = False
    webrtc_vad: webrtcvad.Vad | None = None
    silero_vad: SileroVadLike | None = None


callback_context: RealtimeCallbackContext | None = None


class RecordingStatusEmitter:
    def __init__(self, runtime: RecordingRuntime):
        self._runtime = runtime

    def emit(self, *, status: str, timer: str | None = None, buffer_text: str | None = None, sentences: str | None = None) -> None:
        if not bc.web_bridge:
            return
        bc.web_bridge.update_task_message(status)
        try:
            bc.web_bridge.set_recording_state(
                _build_recording_state_payload(
                    status=status,
                    device=self._runtime.device,
                    lang_source=self._runtime.lang_source,
                    lang_target=self._runtime.lang_target_display,
                    engine=self._runtime.engine,
                    mode=self._runtime.taskname,
                    timer=timer,
                    buffer_text=buffer_text,
                    sentences=sentences,
                )
            )
        except Exception:
            pass


class BufferStateReducer:
    def __init__(
        self,
        *,
        is_tc: bool,
        is_tl: bool,
        tl_engine_whisper: bool,
        sentence_limitless: bool,
        max_sentences: int,
        separator: str,
        translator: TranslationDispatcher,
    ):
        self._is_tc = is_tc
        self._is_tl = is_tl
        self._tl_engine_whisper = tl_engine_whisper
        self._sentence_limitless = sentence_limitless
        self._max_sentences = max_sentences
        self._separator = separator
        self._translator = translator

    def reduce_sentences(self) -> None:
        if self._is_tc and shared_state.prev_tc_res:
            bc.tc_sentences.append(shared_state.prev_tc_res)
        bc.tc_sentences = _enforce_sentence_limits(bc.tc_sentences, self._sentence_limitless, self._max_sentences)
        if bc.tc_sentences:
            bc.update_tc(None, self._separator)
        self._translator.dispatch(None, _build_full_transcribed_text(bc.tc_sentences, None))
        shared_state.prev_tc_res = ""

        if self._is_tl:
            if shared_state.prev_tl_res and self._tl_engine_whisper:
                bc.tl_sentences.append(shared_state.prev_tl_res)
            bc.tl_sentences = _enforce_sentence_limits(bc.tl_sentences, self._sentence_limitless, self._max_sentences)
            if bc.tl_sentences:
                bc.update_tl(None, self._separator)
            shared_state.prev_tl_res = ""


@dataclass
class SmartSplitOutcome:
    pre_audio_bytes: bytes
    post_audio_bytes: bytes
    pre_result: TranscriptionResultLike
    post_result: TranscriptionResultLike


def _build_smart_split_outcome(
    previous_result: TranscriptionResultLike,
    last_sample: bytes,
    *,
    prev_buffer_seconds: float,
    sr_divider: int,
    samp_width: int,
    num_of_channels: int,
) -> SmartSplitOutcome | None:
    if not hasattr(previous_result, "segments"):
        return None

    split_time, pre_segs, post_segs = _calculate_smart_split(
        previous_result.segments,
        (prev_buffer_seconds / 2.0) if prev_buffer_seconds > 0 else 0.0,
    )
    if split_time is None:
        return None

    pre_result = type(previous_result)(pre_segs) if pre_segs else previous_result
    post_result = type(previous_result)(post_segs) if post_segs else previous_result
    bytes_before = max(0, min(int(round(split_time * sr_divider)) * samp_width * num_of_channels, len(last_sample)))
    return SmartSplitOutcome(
        pre_audio_bytes=last_sample[:bytes_before],
        post_audio_bytes=last_sample[bytes_before:],
        pre_result=pre_result,
        post_result=post_result,
    )

shared_state = RealtimeSharedState()

# =========================================================================
# HELPER FUNCTIONS
# =========================================================================

def _enforce_sentence_limits(sentences: list, is_limitless: bool, max_sentences: int) -> list:
    """去重并限制历史句子列表的长度"""
    sentences = unique_rec_list(sentences)
    if not is_limitless and len(sentences) > max_sentences:
        return sentences[-max_sentences:]
    return sentences


def _result_text(value: ResultSnapshot | None) -> str:
    return str(getattr(value, "text", value or "")).strip()


def _build_full_transcribed_text(sentences: list[object], current_res: ResultSnapshot | None) -> str:
    lines = [_result_text(item) for item in sentences if _result_text(item)]
    current_text = _result_text(current_res)
    if current_text:
        lines.append(current_text)
    return "\n".join(lines)


def _build_recording_state_payload(
    *,
    status: str,
    device: str,
    lang_source: str,
    lang_target: str,
    engine: str,
    mode: str,
    timer: str | None = None,
    buffer_text: str | None = None,
    sentences: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": status,
        "device": device,
        "lang_source": lang_source,
        "lang_target": lang_target,
        "engine": engine,
        "mode": mode,
    }
    if timer is not None:
        payload["timer"] = timer
    if buffer_text is not None:
        payload["buffer"] = buffer_text
    if sentences is not None:
        payload["sentences"] = sentences
    return payload


def _build_recording_session_config(
    *,
    rec_type: str,
    lang_source: str,
    engine: str,
    is_tc: bool,
    is_tl: bool,
) -> RecordingSessionConfig:
    return RecordingSessionConfig(
        rec_type=rec_type,
        transcribe_rate=timedelta(seconds=sj.cache["transcribe_rate"] / 1000),
        max_buffer_s=int(sj.cache.get(f"max_buffer_{rec_type}", 10)),
        max_sentences=int(sj.cache.get(f"max_sentences_{rec_type}", 5)),
        sentence_limitless=bool(sj.cache.get(f"{rec_type}_no_limit", False)),
        tl_engine_whisper=engine in model_values,
        taskname="Transcribe & Translate" if is_tc and is_tl else "Transcribe" if is_tc else "Translate",
        auto=lang_source.lower() == "auto detect",
        threshold_enable=bool(sj.cache.get(f"threshold_enable_{rec_type}", True)),
        threshold_db=float(sj.cache.get(f"threshold_db_{rec_type}", -20)),
        threshold_auto=bool(sj.cache.get(f"threshold_auto_{rec_type}", True)),
        use_silero=bool(sj.cache.get(f"threshold_auto_silero_{rec_type}", True)),
        silero_min_conf=float(sj.cache.get(f"threshold_silero_{rec_type}_min", 0.75)),
        auto_break_buffer=bool(sj.cache.get(f"auto_break_buffer_{rec_type}", True)),
        use_temp=bool(sj.cache["use_temp"]),
        separator=str_separator_to_html(literal_eval(quote(sj.cache["separate_with"]))),
    )


def _resolve_live_input_source_language(lang_source: str, engine: str) -> str:
    source_lang = lang_source
    if bc.auto_detected_lang and bc.auto_detected_lang != "~":
        try:
            detected_name = get_whisper_lang_name(bc.auto_detected_lang)
            if verify_language_in_key(detected_name.lower(), engine):
                source_lang = detected_name
        except Exception:
            pass
    return source_lang


def _normalize_translation_result_units(result: TranslationApiResult, source_units: list[str]) -> list[str]:
    result_list = result if isinstance(result, list) else [result]
    return [
        str(result_list[idx]).strip()
        for idx in range(len(source_units))
        if idx < len(result_list) and str(result_list[idx]).strip()
    ]


def _merge_translation_units(aligned_units: list[str]) -> list[str]:
    merged_units: list[str] = []
    for curr in aligned_units:
        if not merged_units:
            merged_units.append(curr)
            continue
        prev = merged_units[-1].rstrip()
        curr = curr.lstrip()

        p_tail, c_head = prev[-1] if prev else "", curr[0] if curr else ""
        if not (p_tail and re.match(r"[^\w\s]", p_tail)) and not (c_head and re.match(r"[^\w\s]", c_head)):
            glue = " " if re.match(r"[A-Za-z0-9]", p_tail) and re.match(r"[A-Za-z0-9]", c_head) else ""
            merged_units[-1] = f"{prev}{glue}{curr}"
        else:
            merged_units.append(curr)
    return merged_units


def _drain_audio_queue() -> None:
    while not bc.data_queue.empty():
        bc.data_queue.get()


def _cleanup_temp_audio_paths(temp_audio_paths: list[str]) -> None:
    for audio in temp_audio_paths:
        try:
            os.remove(audio)
        except Exception:
            pass


def _finalize_recording_session(p, session_state: RealtimeSessionState, update_status_lbl, *, keep_temp: bool) -> None:
    bc.current_rec_status = "⚠️ Stopping stream"
    update_status_lbl()
    if bc.stream:
        bc.stream.stop_stream()
        bc.stream.close()
        bc.stream = None
    bc.rec_tc_thread = bc.rec_tl_thread = None

    bc.current_rec_status = "⚠️ Terminating pyaudio"
    update_status_lbl()
    p.terminate()

    _drain_audio_queue()
    if not keep_temp:
        _cleanup_temp_audio_paths(session_state.temp_audio_paths)

    _reset_callback_context()
    bc.current_rec_status = "⏹️ Stopped"
    update_status_lbl()


class TranslationDispatcher:
    def __init__(
        self,
        *,
        is_tl: bool,
        tl_engine_whisper: bool,
        use_temp: bool,
        keep_temp: bool,
        separator: str,
        lang_source: str,
        lang_target: str,
        engine: str,
        hallucination_filters,
        stable_tl,
        whisper_args,
        record_status_updater,
    ):
        self._is_tl = is_tl
        self._tl_engine_whisper = tl_engine_whisper
        self._use_temp = use_temp
        self._keep_temp = keep_temp
        self._separator = separator
        self._lang_source = lang_source
        self._lang_target = lang_target
        self._engine = engine
        self._hallucination_filters = hallucination_filters
        self._stable_tl = stable_tl
        self._whisper_args = whisper_args
        self._record_status_updater = record_status_updater
        self._queue: Queue[TranslationTask] = Queue()
        self._lock = Lock()
        self._latest_api_task: TranslationTask | None = None
        self._inflight_api_text = ""

    def dispatch(self, audio_target: AudioTarget | None, text_snapshot: str) -> None:
        if not self._is_tl:
            return
        if self._tl_engine_whisper:
            self._queue.put(
                TranslationTask(
                    kind="whisper",
                    audio=audio_target,
                    separator=self._separator,
                    cleanup_audio=not self._keep_temp and isinstance(audio_target, str),
                )
            )
            return

        text_key = text_snapshot.strip()
        if not text_key:
            return
        with self._lock:
            if text_key != self._inflight_api_text:
                self._latest_api_task = TranslationTask(
                    kind="api",
                    text=text_key,
                    lang_source=self._lang_source,
                    lang_target=self._lang_target,
                    engine=self._engine,
                    separator=self._separator,
                )

    def close(self, running_flag_getter, cleanup_audio_fn) -> None:
        while running_flag_getter() or not self._queue.empty() or self._latest_api_task is not None:
            if not running_flag_getter():
                while not self._queue.empty():
                    task = self._queue.get_nowait()
                    if task.cleanup_audio:
                        cleanup_audio_fn(task.audio)
                break

            task: TranslationTask | None = None
            try:
                task = self._queue.get(timeout=0.1)
            except Empty:
                with self._lock:
                    if self._latest_api_task:
                        task, self._latest_api_task = self._latest_api_task, None
                        self._inflight_api_text = task.text

            if not task:
                continue

            try:
                self._record_status_updater()
                if task.kind == "whisper":
                    run_whisper_tl(task.audio, self._stable_tl, task.separator, self._hallucination_filters, **self._whisper_args)
                else:
                    tl_api(task.text, task.lang_source, task.lang_target, task.engine, task.separator)
            except Exception as exc:
                logger.exception(exc)
            finally:
                if task.kind == "api":
                    with self._lock:
                        self._inflight_api_text = ""
                if task.cleanup_audio:
                    cleanup_audio_fn(task.audio)
                self._record_status_updater()


def _calculate_buffer_duration(
    audio_bytes: bytes,
    *,
    samp_width: int,
    num_of_channels: int,
    sr_divider: int,
) -> float:
    if samp_width <= 0 or num_of_channels <= 0 or sr_divider <= 0:
        return 0.0
    return len(audio_bytes) / (samp_width * num_of_channels * sr_divider)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _get_callback_context() -> RealtimeCallbackContext | None:
    return callback_context


def _reset_callback_context() -> None:
    global callback_context
    callback_context = None


def _initialize_callback_context(
    *,
    sample_rate: int,
    chunk_size: int,
    threshold_enable: bool,
    threshold_db: float,
    threshold_auto: bool,
    use_silero: bool,
    silero_min_conf: float,
    num_of_channels: int,
    samp_width: int,
    use_temp: bool,
    webrtc_vad: webrtcvad.Vad,
    silero_vad: SileroVadLike,
) -> RealtimeCallbackContext:
    global callback_context

    callback_context = RealtimeCallbackContext(
        sample_rate=sample_rate,
        frame_duration_ms=get_frame_duration(sample_rate, chunk_size),
        threshold_enable=threshold_enable,
        threshold_db=threshold_db,
        threshold_auto=threshold_auto,
        use_silero=use_silero,
        silero_min_conf=silero_min_conf,
        vad_checked=False,
        num_of_channels=num_of_channels,
        samp_width=samp_width,
        use_temp=use_temp,
        silence_started_at=time(),
        webrtc_vad=webrtc_vad,
        silero_vad=silero_vad,
    )
    return callback_context


def _prime_realtime_vad(ctx: RealtimeCallbackContext, resampled: bytes) -> None:
    if ctx.vad_checked:
        return

    ctx.vad_checked = True
    try:
        get_speech_webrtc(resampled, WHISPER_SR, ctx.frame_duration_ms, ctx.webrtc_vad)
    except Exception:
        pass
    try:
        sil_probe = to_silero(resampled, ctx.num_of_channels, ctx.samp_width)
        if sil_probe.numel() >= 512:
            ctx.silero_vad(sil_probe, WHISPER_SR)
    except Exception:
        pass


def _detect_realtime_speech(ctx: RealtimeCallbackContext, in_data: bytes, resampled: bytes) -> tuple[bool, bytes]:
    data_to_queue = resampled if not ctx.use_temp else in_data
    _prime_realtime_vad(ctx, resampled)

    if not ctx.threshold_enable:
        return True, data_to_queue

    db = get_db(in_data)
    shared_state.last_db = db
    if db > ctx.max_db:
        ctx.max_db = db
    elif db < ctx.min_db:
        ctx.min_db = db

    is_speech = False
    if ctx.threshold_auto:
        try:
            is_speech = bool(get_speech_webrtc(resampled, WHISPER_SR, ctx.frame_duration_ms, ctx.webrtc_vad))
            if is_speech and ctx.use_silero and not ctx.silero_disabled:
                sil_data = to_silero(resampled, ctx.num_of_channels, ctx.samp_width)
                if sil_data.numel() >= 512:
                    conf = float(ctx.silero_vad(sil_data, WHISPER_SR).item())
                    is_speech = conf >= ctx.silero_min_conf
        except Exception:
            pass
    else:
        is_speech = db > ctx.threshold_db

    return is_speech, data_to_queue


def _update_realtime_queue_state(ctx: RealtimeCallbackContext, *, is_speech: bool, data_to_queue: bytes) -> None:
    if is_speech:
        bc.data_queue.put(data_to_queue)
        ctx.was_recording = True
        if ctx.is_silence:
            ctx.is_silence = False
            ctx.silence_started_at = 0.0
        return

    bc.current_rec_status = "▶️ Recording (Waiting for speech)"
    if ctx.was_recording:
        ctx.was_recording = False
        if not ctx.is_silence:
            ctx.is_silence = True
            ctx.silence_started_at = time()


def _handle_record_callback_error(ctx: RealtimeCallbackContext | None, exc: Exception) -> None:
    message = str(exc)
    if "Input audio chunk is too short" not in message:
        logger.error(f"record_cb error: {message}")
    if ctx and "Error while processing frame" in message:
        if ctx.frame_duration_ms >= 20:
            ctx.frame_duration_ms -= 10
            ctx.vad_checked = False
        else:
            ctx.threshold_auto = False


def _build_record_audio_target(
    session_state: RealtimeSessionState,
    *,
    use_temp: bool,
    num_of_channels: int,
    samp_width: int,
    demucs_enabled: bool,
    cuda_device: str,
    sr_ori: int,
) -> AudioTarget:
    if not use_temp:
        wf = BytesIO()
        with w_open(wf, "wb") as wav_writer:
            wav_writer.setframerate(WHISPER_SR)
            wav_writer.setsampwidth(samp_width)
            wav_writer.setnchannels(num_of_channels)
            wav_writer.writeframes(session_state.last_sample)
        wf.seek(0)

        with w_open(wf, "rb") as wav_reader:
            audio_bytes = wav_reader.readframes(wav_reader.getnframes())
        return _bytes_to_numpy(audio_bytes, num_of_channels, demucs_enabled, cuda_device)

    audio_target = _save_to_temp(session_state.last_sample, num_of_channels, samp_width, sr_ori)
    session_state.temp_audio_paths.append(audio_target)
    return audio_target


def _execute_realtime_transcription(
    audio_target: AudioTarget,
    stable_tc: WhisperCallable,
    whisper_args: dict[str, object],
) -> TranscriptionResultLike | None:
    try:
        if bc.tc_lock:
            with cast(LockLike, bc.tc_lock):
                return stable_tc(audio_target, task="transcribe", **whisper_args)
        return stable_tc(audio_target, task="transcribe", **whisper_args)
    except Exception as exc:
        logger.warning(f"Transcribing error: {exc}")
        return None


def _filter_realtime_transcription_result(
    result: TranscriptionResultLike | None,
    *,
    hallucination_filters: dict[str, object],
    auto: bool,
    configured_language: str | None,
) -> TranscriptionResultLike | None:
    if not (sj.cache["filter_rec"] and result):
        return result

    try:
        filter_language = get_whisper_lang_name(result.language) if auto else configured_language
        if not filter_language:
            return result
        return remove_segments_by_str(
            result,
            hallucination_filters.get(filter_language, []),
            sj.cache["filter_rec_case_sensitive"],
            sj.cache["filter_rec_strip"],
            sj.cache["filter_rec_ignore_punctuations"],
            sj.cache["filter_rec_exact_match"],
            sj.cache["filter_rec_similarity"],
            sj.cache["debug_realtime_record"],
        )
    except Exception:
        return result


def _commit_realtime_transcription(
    result: TranscriptionResultLike | None,
    *,
    audio_target: AudioTarget,
    is_tl: bool,
    separator: str,
    translator: TranslationDispatcher,
) -> None:
    text = result.text.strip() if result else ""
    bc.auto_detected_lang = result.language if result else "~"

    if not text:
        bc.current_rec_status = "▶️ Recording"
        return

    shared_state.prev_tc_res = result
    bc.update_tc(result, separator)
    bc.current_rec_status = "▶️ Recording ⟳ Translating text" if is_tl else "▶️ Recording"
    translator.dispatch(audio_target, _build_full_transcribed_text(bc.tc_sentences, result))


def _save_to_temp(audio_bytes: bytes, channels: int, samp_width: int, sr: int) -> str:
    """将音频字节流保存为临时 WAV 文件并返回路径"""
    wf = BytesIO()
    with w_open(wf, 'wb') as wav_writer:
        wav_writer.setframerate(sr)
        wav_writer.setsampwidth(samp_width)
        wav_writer.setnchannels(channels)
        wav_writer.writeframes(audio_bytes)
    
    path = generate_temp_filename(dir_temp)
    with open(path, 'wb') as f:
        f.write(wf.getvalue())
    return path

def _bytes_to_numpy(audio_bytes: bytes, channels: int, use_demucs: bool, device: str) -> np.ndarray | torch.Tensor:
    """将 PCM 字节流转换为 Whisper/Demucs 需要的 Numpy 数组或 Tensor"""
    audio_as_np_int16 = np.frombuffer(audio_bytes, dtype=np.int16).flatten()
    audio_as_np_float32 = audio_as_np_int16.astype(np.float32)
    max_int16 = 32768.0
    
    if channels == 1:
        audio_np = audio_as_np_float32 / max_int16
    else:
        chunk_length = len(audio_as_np_float32) // channels
        audio_reshaped = np.reshape(audio_as_np_float32, (chunk_length, channels))
        audio_np = audio_reshaped[:, 0] / max_int16  # 取左声道
        
    if use_demucs:
        return torch.from_numpy(audio_np).to(device)
    return audio_np

def _calculate_smart_split(
    segments: list,
    half_point_time: float,
) -> tuple[float | None, list[dict[str, object]], list[dict[str, object]]]:
    """核心算法：在 Whisper 结果的后半段寻找最大的无声间隙，返回切割点时间及切割后的前后段字典"""
    word_infos = []
    for sidx, seg in enumerate(segments):
        for widx, w in enumerate(seg.to_dict().get('words', [])):
            text_w = str(w.get('word', w.get('text', ''))).strip()
            if not text_w: 
                continue
            try:
                start = float(w.get('start', w.get('end', 0.0)))
                end = float(w.get('end', start))
            except Exception:
                continue
            word_infos.append((sidx, widx, text_w, (start + end) / 2.0, start, end))

    filtered_words = [wi for wi in word_infos if wi[3] >= half_point_time]
    
    max_gap, max_idx = -1.0, None
    for i in range(len(filtered_words) - 1):
        gap = filtered_words[i + 1][4] - filtered_words[i][5]
        if gap > max_gap:
            max_gap = gap
            max_idx = i

    if max_idx is None or max_gap <= 0:
        return None, [], []

    left_word, right_word = filtered_words[max_idx], filtered_words[max_idx + 1]
    seg_l, seg_r = left_word[0], right_word[0]
    split_time = (left_word[5] + right_word[4]) / 2.0

    pre_segs, post_segs = [], []
    if seg_l != seg_r:
        pre_segs = [s.to_dict() for s in segments[:seg_l + 1]]
        post_segs = [s.to_dict() for s in segments[seg_l + 1:]]
    else:
        for i, seg in enumerate(segments):
            seg_d = seg.to_dict()
            if i < seg_l:
                pre_segs.append(seg_d)
            elif i > seg_l:
                post_segs.append(seg_d)
            else:
                words = seg_d.get('words', [])
                pre_w = [w for w in words if (float(w.get('start', 0.0)) + float(w.get('end', 0.0))) / 2.0 < split_time]
                post_w = [w for w in words if (float(w.get('start', 0.0)) + float(w.get('end', 0.0))) / 2.0 >= split_time]
                
                if pre_w:
                    d = deepcopy(seg_d)
                    d['words'], d['text'] = pre_w, " ".join([w.get('word', w.get('text', '')).strip() for w in pre_w]).strip()
                    d['start'], d['end'] = seg_d.get('start', pre_w[0].get('start', 0.0)), pre_w[-1].get('end', split_time)
                    if d['start'] > d['end']: d['start'] = d['end']
                    pre_segs.append(d)
                if post_w:
                    d = deepcopy(seg_d)
                    d['words'], d['text'] = post_w, " ".join([w.get('word', w.get('text', '')).strip() for w in post_w]).strip()
                    d['start'], d['end'] = post_w[0].get('start', split_time), seg_d.get('end', post_w[-1].get('end', split_time))
                    if d['start'] > d['end']: d['start'] = d['end']
                    post_segs.append(d)

    return split_time, pre_segs, post_segs


def _apply_smart_split(
    *,
    session_state: RealtimeSessionState,
    previous_result: TranscriptionResultLike,
    sr_divider: int,
    samp_width: int,
    num_of_channels: int,
    sentence_limitless: bool,
    max_sentences: int,
    separator: str,
    translator: TranslationDispatcher,
) -> bool:
    split_outcome = _build_smart_split_outcome(
        previous_result,
        session_state.last_sample,
        prev_buffer_seconds=session_state.prev_tc_buffer_seconds,
        sr_divider=sr_divider,
        samp_width=samp_width,
        num_of_channels=num_of_channels,
    )
    if split_outcome is None:
        return False

    try:
        session_state.last_sample = split_outcome.post_audio_bytes
        pre_audio_path = _save_to_temp(
            split_outcome.pre_audio_bytes,
            num_of_channels,
            samp_width,
            sr_divider,
        )

        bc.tc_sentences.append(split_outcome.pre_result)
        session_state.recalculate_duration(
            samp_width=samp_width,
            num_of_channels=num_of_channels,
            sr_divider=sr_divider,
        )
        session_state.next_transcribe_time = _utc_now()
        shared_state.prev_tc_res = split_outcome.post_result

        bc.tc_sentences = _enforce_sentence_limits(bc.tc_sentences, sentence_limitless, max_sentences)
        bc.update_tc(shared_state.prev_tc_res, separator)
        translator.dispatch(pre_audio_path, _build_full_transcribed_text(bc.tc_sentences, shared_state.prev_tc_res))
        return True
    except Exception as exc:
        logger.warning(f"Smart-Split fallback due to error: {exc}")
        return False


def _break_buffer_and_update_state(
    *,
    reason: str,
    session_state: RealtimeSessionState,
    is_tc: bool,
    sr_divider: int,
    samp_width: int,
    num_of_channels: int,
    sentence_limitless: bool,
    max_sentences: int,
    separator: str,
    translator: TranslationDispatcher,
    buffer_reducer: BufferStateReducer,
) -> None:
    logger.info(
        f"Buffer break [{reason}] | bytes={len(session_state.last_sample)} dur={session_state.duration_seconds:.2f}s"
    )

    preserved_tc = (
        reason == "buffer_full"
        and is_tc
        and bool(shared_state.prev_tc_res)
        and hasattr(shared_state.prev_tc_res, "segments")
        and _apply_smart_split(
            session_state=session_state,
            previous_result=shared_state.prev_tc_res,
            sr_divider=sr_divider,
            samp_width=samp_width,
            num_of_channels=num_of_channels,
            sentence_limitless=sentence_limitless,
            max_sentences=max_sentences,
            separator=separator,
            translator=translator,
        )
    )

    if preserved_tc:
        return

    buffer_reducer.reduce_sentences()
    session_state.reset_buffer()

# =========================================================================
# MAIN SESSION
# =========================================================================

def record_session(
    lang_source: str, lang_target: str, engine: str, model_name_tc: str, device: str, is_tc: bool, is_tl: bool, speaker: bool = False
) -> None:
    """实时录音、语音识别与翻译核心总管"""
    rec_type = "speaker" if speaker else "mic"

    try:
        config = _build_recording_session_config(
            rec_type=rec_type,
            lang_source=lang_source,
            engine=engine,
            is_tc=is_tc,
            is_tl=is_tl,
        )
        p = pyaudio.PyAudio()
        success, detail = get_device_details(rec_type, sj, p)
        if not success:
            raise Exception("Failed to get device details")

        device_detail = detail["device_detail"]
        sr_ori, num_of_channels, chunk_size = detail["sample_rate"], detail["num_of_channels"], detail["chunk_size"]

        if not sj.cache["supress_record_warning"] and sr_ori > 48000:
            logger.warning(f"Sample rate is high ({sr_ori} Hz). May cause issues. Can be suppressed in settings.")
        # if is_tl and not tl_engine_whisper:
        #     try: requests.get("https://www.google.com/", timeout=5)
        #     except Exception: logger.warning("No internet connection detected. API Translation might fail.")

        webrtc_vad = webrtcvad.Vad(sj.cache.get(f"threshold_auto_mode_{rec_type}", 3))
        
        if callable(getattr(torchaudio, "set_audio_backend", None)):
            try: torchaudio.set_audio_backend("soundfile") # type: ignore
            except Exception: pass
                
        silero_model = torch.hub.load(repo_or_dir=dir_silero_vad, source="local", model="silero_vad", onnx=True)
        silero_vad = cast(SileroVadLike, silero_model[0] if isinstance(silero_model, tuple) else silero_model)
        silero_vad.reset_states()

        bc.tc_lock = Lock() if (is_tc and is_tl and config.tl_engine_whisper) else None

        # Load models
        model_args = get_model_args(sj.cache)
        _, _, stable_tc, stable_tl, to_args = get_model(is_tc, is_tl, config.tl_engine_whisper, model_name_tc, engine, sj.cache, **model_args)
        whisper_args = get_tc_args(to_args, sj.cache)
        whisper_args["verbose"] = None
        configured_whisper_language = get_whisper_lang_similar(lang_source) if not config.auto else None
        whisper_args["language"] = TO_LANGUAGE_CODE.get(configured_whisper_language) if configured_whisper_language else None

        if sj.cache.get("enable_initial_prompt", False):
            from ..whisper.prompts import pick_initial_prompt
            prompt = pick_initial_prompt(whisper_args.get("language"), True, sj.cache.get("initial_prompts_map", {}), None)
            if prompt: whisper_args["initial_prompt"] = prompt
            else: whisper_args.pop("initial_prompt", None)
        else:
            whisper_args.pop("initial_prompt", None)

        demucs_enabled, vad_enabled = bool(whisper_args.get("demucs", False)), bool(whisper_args.get("vad", False))
        if sj.cache["use_faster_whisper"] and not config.use_temp: whisper_args["input_sr"] = WHISPER_SR
        if demucs_enabled and vad_enabled:
            config.use_temp = True  # Force temp file
        
        hallucination_filters = get_hallucination_filter('rec', sj.cache["path_filter_rec"]) if sj.cache["filter_rec"] else {}
        cuda_device = model_args["device"]

        logger.info(f"Session starting: {config.taskname} | Engine: {engine} | Device: {cuda_device} | Demucs: {demucs_enabled}")

        # UI & State Updaters
        t_start = time()
        session_state = RealtimeSessionState()
        bc.current_rec_status, bc.auto_detected_lang = "▶️ Recording (Waiting for speech)", "~"
        runtime = RecordingRuntime(
            taskname=config.taskname,
            device=device,
            lang_source=lang_source,
            lang_target=lang_target,
            engine=engine,
            is_tl=is_tl,
            use_temp=config.use_temp,
            separator=config.separator,
            keep_temp=bool(sj.cache.get("keep_temp", False)),
            t_start=t_start,
            max_buffer_s=config.max_buffer_s,
            max_sentences=config.max_sentences,
            sentence_limitless=config.sentence_limitless,
            lang_target_display=lang_target if is_tl else "-",
        )
        status_emitter = RecordingStatusEmitter(runtime)

        def update_status_lbl() -> None:
            status_emitter.emit(status=bc.current_rec_status)

        def update_web_ui():
            while bc.recording:
                if session_state.paused:
                    sleep(0.1)
                    continue
                try:
                    sentence_count_text = f"{len(bc.tc_sentences) or len(bc.tl_sentences) or '0'}"
                    if not config.sentence_limitless:
                        sentence_count_text += f"/{config.max_sentences}"
                    status_emitter.emit(
                        status=bc.current_rec_status,
                        timer=strftime("%H:%M:%S", gmtime(time() - t_start)),
                        buffer_text=f"{round(session_state.duration_seconds, 2)}/{round(config.max_buffer_s, 2)} sec",
                        sentences=sentence_count_text,
                    )
                    sleep(0.1)
                except Exception:
                    break

        def cleanup_translation_audio(audio_target: AudioTarget | None) -> None:
            if isinstance(audio_target, str):
                try:
                    os.remove(audio_target)
                except Exception:
                    pass

        translator = TranslationDispatcher(
            is_tl=is_tl,
            tl_engine_whisper=config.tl_engine_whisper,
            use_temp=config.use_temp,
            keep_temp=runtime.keep_temp,
            separator=config.separator,
            lang_source=lang_source,
            lang_target=lang_target,
            engine=engine,
            hallucination_filters=hallucination_filters,
            stable_tl=stable_tl,
            whisper_args=whisper_args,
            record_status_updater=update_status_lbl,
        )
        buffer_reducer = BufferStateReducer(
            is_tc=is_tc,
            is_tl=is_tl,
            tl_engine_whisper=config.tl_engine_whisper,
            sentence_limitless=config.sentence_limitless,
            max_sentences=config.max_sentences,
            separator=config.separator,
            translator=translator,
        )

        Thread(target=lambda: translator.close(lambda: bool(bc.recording), cleanup_translation_audio), daemon=True).start()

        update_status_lbl()
        Thread(target=update_web_ui, daemon=True).start()

        # Audio stream setup
        bc.tc_sentences, bc.tl_sentences = [], []
        shared_state.prev_tc_res, shared_state.prev_tl_res = "", ""
        samp_width = p.get_sample_size(pyaudio.paInt16)
        sr_divider = WHISPER_SR if not config.use_temp else sr_ori
        callback_ctx = _initialize_callback_context(
            sample_rate=sr_ori,
            chunk_size=chunk_size,
            threshold_enable=config.threshold_enable,
            threshold_db=config.threshold_db,
            threshold_auto=config.threshold_auto,
            use_silero=config.use_silero,
            silero_min_conf=config.silero_min_conf,
            num_of_channels=num_of_channels,
            samp_width=samp_width,
            use_temp=config.use_temp,
            webrtc_vad=webrtc_vad,
            silero_vad=silero_vad,
        )
        
        bc.stream = p.open(format=pyaudio.paInt16, channels=num_of_channels, rate=sr_ori, input=True, frames_per_buffer=chunk_size, input_device_index=int(device_detail["index"]), stream_callback=record_cb)

        # Main Transcribing Loop
        while bc.recording:
            if session_state.paused:
                sleep(0.1)
                continue

            try:
                data = bc.data_queue.get(timeout=0.1)
            except Empty:
                if (
                    config.auto_break_buffer
                    and callback_ctx.is_silence
                    and time() - callback_ctx.silence_started_at > 1
                ):
                    callback_ctx.is_silence = False
                    _break_buffer_and_update_state(
                        reason="silence",
                        session_state=session_state,
                        is_tc=is_tc,
                        sr_divider=sr_divider,
                        samp_width=samp_width,
                        num_of_channels=num_of_channels,
                        sentence_limitless=config.sentence_limitless,
                        max_sentences=config.max_sentences,
                        separator=config.separator,
                        translator=translator,
                        buffer_reducer=buffer_reducer,
                    )
                    bc.current_rec_status = "▶️ Recording (Waiting for speech)"
                if (
                    not session_state.last_sample
                    or (
                        session_state.next_transcribe_time
                        and session_state.next_transcribe_time > _utc_now()
                    )
                ):
                    continue
                data = b""

            now = _utc_now()
            if not session_state.next_transcribe_time:
                session_state.next_transcribe_time = now + config.transcribe_rate

            session_state.append_audio(data)
            while not bc.data_queue.empty():
                session_state.append_audio(bc.data_queue.get_nowait())

            if session_state.next_transcribe_time > now:
                continue
            session_state.next_transcribe_time = now + config.transcribe_rate

            session_state.recalculate_duration(
                samp_width=samp_width,
                num_of_channels=num_of_channels,
                sr_divider=sr_divider,
            )
            if session_state.duration_seconds < sj.cache.get(f"min_input_length_{rec_type}", 0.4):
                continue

            audio_target = _build_record_audio_target(
                session_state,
                use_temp=config.use_temp,
                num_of_channels=num_of_channels,
                samp_width=samp_width,
                demucs_enabled=demucs_enabled,
                cuda_device=cuda_device,
                sr_ori=sr_ori,
            )

            # Execution logic
            if is_tl and config.tl_engine_whisper and not is_tc:
                bc.current_rec_status = "▶️ Recording ⟳ Translating Audio"
                translator.dispatch(audio_target, "")
            else:
                bc.current_rec_status = "▶️ Recording ⟳ Transcribing Audio"
                session_state.prev_tc_buffer_seconds = session_state.duration_seconds

                result = _execute_realtime_transcription(audio_target, stable_tc, whisper_args)
                if result is None:
                    continue

                result = _filter_realtime_transcription_result(
                    result,
                    hallucination_filters=hallucination_filters,
                    auto=config.auto,
                    configured_language=configured_whisper_language,
                )
                _commit_realtime_transcription(
                    result,
                    audio_target=audio_target,
                    is_tl=is_tl,
                    separator=config.separator,
                    translator=translator,
                )

            # Cleanup Temp Audio
            if config.use_temp and not sj.cache.get("keep_temp", False) and isinstance(audio_target, str):
                if not (is_tl and config.tl_engine_whisper):
                    try:
                        os.remove(audio_target)
                        session_state.temp_audio_paths.remove(audio_target)
                    except Exception:
                        pass

            if session_state.duration_seconds > config.max_buffer_s:
                _break_buffer_and_update_state(
                    reason="buffer_full",
                    session_state=session_state,
                    is_tc=is_tc,
                    sr_divider=sr_divider,
                    samp_width=samp_width,
                    num_of_channels=num_of_channels,
                    sentence_limitless=config.sentence_limitless,
                    max_sentences=config.max_sentences,
                    separator=config.separator,
                    translator=translator,
                    buffer_reducer=buffer_reducer,
                )
            if bc.current_rec_status == "▶️ Recording ⟳ Transcribing Audio":
                bc.current_rec_status = "▶️ Recording"

        _finalize_recording_session(p, session_state, update_status_lbl, keep_temp=runtime.keep_temp)

    except Exception as e:
        logger.error(f"Error in record session: {str(e)}")
    finally:
        torch.cuda.empty_cache()
        logger.info("Record session ended")


def record_cb(in_data, _frame_count, _time_info, _status):
    """Audio stream callback for PyAudio"""
    ctx = _get_callback_context()
    try:
        if ctx is None:
            return (in_data, pyaudio.paContinue)

        resampled = resample_sr(in_data, ctx.sample_rate, WHISPER_SR)
        is_speech, data_to_queue = _detect_realtime_speech(ctx, in_data, resampled)
        _update_realtime_queue_state(ctx, is_speech=is_speech, data_to_queue=data_to_queue)

        return (in_data, pyaudio.paContinue)
    except Exception as exc:
        _handle_record_callback_error(ctx, exc)
        return (in_data, pyaudio.paContinue)

# =========================================================================
# API / WORKER EXECUTORS
# =========================================================================

def run_whisper_tl(
    audio: AudioTarget | None,
    stable_tl: WhisperCallable,
    separator: str,
    hallucination_filters: HallucinationFilters,
    **whisper_args,
):
    """Run Whisper translation task"""
    try:
        result = stable_tl(audio, task="translate", **whisper_args)
        if sj.cache["filter_rec"]:
            result = remove_segments_by_str(
                result, hallucination_filters.get("english", []), sj.cache["filter_rec_case_sensitive"],
                sj.cache["filter_rec_strip"], sj.cache["filter_rec_ignore_punctuations"], 
                sj.cache["filter_rec_exact_match"], sj.cache["filter_rec_similarity"], False
            )
        text = result.text.strip()
        bc.auto_detected_lang = result.language or "~"
        if text:
            shared_state.prev_tl_res = result
            bc.update_tl(result, separator)
    except Exception as e:
        logger.error(f"Whisper TL Error: {e}")

def tl_api(text: str, lang_source: str, lang_target: str, engine: str, separator: str):
    """Run Network API translation task"""
    try:
        source_units = [line.strip() for line in text.splitlines() if line.strip()]
        if not source_units: return

        kwargs = {"live_input": True}
        source_lang = _resolve_live_input_source_language(lang_source, engine)

        if engine == "LibreTranslate":
            kwargs.update({"libre_link": sj.cache["libre_link"], "libre_api_key": sj.cache["libre_api_key"]})

        success, result = translate(engine, source_units, source_lang, lang_target, get_proxies(sj.cache["http_proxy"], sj.cache["https_proxy"]), False, **kwargs)
        if not success: raise Exception(result)

        aligned_units = _normalize_translation_result_units(cast(TranslationApiResult, result), source_units)

        if not aligned_units: return

        if engine == "Selenium Chrome Translate":
            bc.tl_sentences, shared_state.prev_tl_res = aligned_units, ""
            bc.update_tl(None, separator)
            return

        bc.tl_sentences, shared_state.prev_tl_res = _merge_translation_units(aligned_units) or aligned_units, ""
        bc.update_tl(None, separator)
    except Exception as e:
        logger.error(f"API Translation ({engine}) failed: {str(e)}")
