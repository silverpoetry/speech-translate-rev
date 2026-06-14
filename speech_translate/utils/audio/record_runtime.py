from __future__ import annotations

import re
from queue import Empty, Queue
from threading import Lock

from speech_translate._logging import logger
from speech_translate.linker import bc, sj
from speech_translate.utils.audio.record_types import (
    AudioTarget,
    HallucinationFilters,
    RecordingRuntime,
    RealtimeSharedState,
    ResultSnapshot,
    TranslationApiResult,
    TranslationTask,
    WhisperCallable,
)
from speech_translate.utils.translate.language import get_whisper_lang_name, verify_language_in_key

from ..helper import get_proxies, unique_rec_list
from ..translate.translator import translate
from ..whisper.result import remove_segments_by_str


def _enforce_sentence_limits(sentences: list, is_limitless: bool, max_sentences: int) -> list:
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


shared_state = RealtimeSharedState()


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


def run_whisper_tl(
    audio: AudioTarget | None,
    stable_tl: WhisperCallable,
    separator: str,
    hallucination_filters: HallucinationFilters,
    **whisper_args,
):
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
    try:
        source_units = [line.strip() for line in text.splitlines() if line.strip()]
        if not source_units:
            return

        kwargs = {"live_input": True}
        source_lang = _resolve_live_input_source_language(lang_source, engine)

        if engine == "LibreTranslate":
            kwargs.update({"libre_link": sj.cache["libre_link"], "libre_api_key": sj.cache["libre_api_key"]})

        success, result = translate(
            engine,
            source_units,
            source_lang,
            lang_target,
            get_proxies(sj.cache["http_proxy"], sj.cache["https_proxy"]),
            False,
            **kwargs,
        )
        if not success:
            raise Exception(result)

        aligned_units = _normalize_translation_result_units(result, source_units)
        if not aligned_units:
            return

        if engine == "Selenium Chrome Translate":
            bc.tl_sentences, shared_state.prev_tl_res = aligned_units, ""
            bc.update_tl(None, separator)
            return

        bc.tl_sentences, shared_state.prev_tl_res = _merge_translation_units(aligned_units) or aligned_units, ""
        bc.update_tl(None, separator)
    except Exception as e:
        logger.error(f"API Translation ({engine}) failed: {str(e)}")
