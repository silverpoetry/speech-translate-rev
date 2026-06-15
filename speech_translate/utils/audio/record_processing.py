from __future__ import annotations

from copy import deepcopy
from io import BytesIO
from wave import open as w_open

import numpy as np

from speech_translate._constants import WHISPER_SR
from speech_translate._logging import logger
from speech_translate._path import dir_temp
from speech_translate.linker import bc, sj
from speech_translate.runtime_deps import torch_from_numpy
from speech_translate.utils.audio.record_runtime import (
    _build_full_transcribed_text,
    _enforce_sentence_limits,
    shared_state,
)
from speech_translate.utils.audio.record_types import (
    AudioTarget,
    HallucinationFilters,
    LockLike,
    RealtimeSessionState,
    SmartSplitOutcome,
    TranscriptionResultLike,
    WhisperCallable,
)

from ..helper import generate_temp_filename
from ..whisper.result import remove_segments_by_str

if False:
    from speech_translate.utils.audio.record_runtime import BufferStateReducer, TranslationDispatcher


def save_to_temp(audio_bytes: bytes, channels: int, samp_width: int, sr: int) -> str:
    wf = BytesIO()
    with w_open(wf, "wb") as wav_writer:
        wav_writer.setframerate(sr)
        wav_writer.setsampwidth(samp_width)
        wav_writer.setnchannels(channels)
        wav_writer.writeframes(audio_bytes)

    path = generate_temp_filename(dir_temp)
    with open(path, "wb") as handle:
        handle.write(wf.getvalue())
    return path


def bytes_to_numpy(audio_bytes: bytes, channels: int, use_demucs: bool, device: str) -> np.ndarray | object:
    audio_as_np_int16 = np.frombuffer(audio_bytes, dtype=np.int16).flatten()
    audio_as_np_float32 = audio_as_np_int16.astype(np.float32)
    max_int16 = 32768.0

    if channels == 1:
        audio_np = audio_as_np_float32 / max_int16
    else:
        chunk_length = len(audio_as_np_float32) // channels
        audio_reshaped = np.reshape(audio_as_np_float32, (chunk_length, channels))
        audio_np = audio_reshaped[:, 0] / max_int16

    if use_demucs:
        return torch_from_numpy(audio_np).to(device)
    return audio_np


def build_record_audio_target(
    session_state: RealtimeSessionState,
    *,
    use_temp: bool,
    num_of_channels: int,
    samp_width: int,
    demucs_enabled: bool,
    cuda_device: str,
    sr_ori: int,
    save_to_temp_fn=save_to_temp,
    bytes_to_numpy_fn=bytes_to_numpy,
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
        return bytes_to_numpy_fn(audio_bytes, num_of_channels, demucs_enabled, cuda_device)

    audio_target = save_to_temp_fn(session_state.last_sample, num_of_channels, samp_width, sr_ori)
    session_state.temp_audio_paths.append(audio_target)
    return audio_target


def execute_realtime_transcription(
    audio_target: AudioTarget,
    stable_tc: WhisperCallable,
    whisper_args: dict[str, object],
) -> TranscriptionResultLike | None:
    try:
        if bc.tc_lock:
            with bc.tc_lock:  # type: ignore[union-attr]
                return stable_tc(audio_target, task="transcribe", **whisper_args)
        return stable_tc(audio_target, task="transcribe", **whisper_args)
    except Exception as exc:
        logger.warning(f"Transcribing error: {exc}")
        return None


def filter_realtime_transcription_result(
    result: TranscriptionResultLike | None,
    *,
    hallucination_filters: HallucinationFilters,
    auto: bool,
    configured_language: str | None,
    get_whisper_lang_name,
    remove_segments_by_str_fn=remove_segments_by_str,
) -> TranscriptionResultLike | None:
    if not (sj.cache["filter_rec"] and result):
        return result

    try:
        filter_language = get_whisper_lang_name(result.language) if auto else configured_language
        if not filter_language:
            return result
        return remove_segments_by_str_fn(
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


def commit_realtime_transcription(
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


def calculate_smart_split(
    segments: list,
    half_point_time: float,
) -> tuple[float | None, list[dict[str, object]], list[dict[str, object]]]:
    word_infos = []
    for sidx, seg in enumerate(segments):
        for widx, word in enumerate(seg.to_dict().get("words", [])):
            text_w = str(word.get("word", word.get("text", ""))).strip()
            if not text_w:
                continue
            try:
                start = float(word.get("start", word.get("end", 0.0)))
                end = float(word.get("end", start))
            except Exception:
                continue
            word_infos.append((sidx, widx, text_w, (start + end) / 2.0, start, end))

    filtered_words = [word_info for word_info in word_infos if word_info[3] >= half_point_time]

    max_gap, max_idx = -1.0, None
    for idx in range(len(filtered_words) - 1):
        gap = filtered_words[idx + 1][4] - filtered_words[idx][5]
        if gap > max_gap:
            max_gap = gap
            max_idx = idx

    if max_idx is None or max_gap <= 0:
        return None, [], []

    left_word, right_word = filtered_words[max_idx], filtered_words[max_idx + 1]
    seg_l, seg_r = left_word[0], right_word[0]
    split_time = (left_word[5] + right_word[4]) / 2.0

    pre_segs, post_segs = [], []
    if seg_l != seg_r:
        pre_segs = [segment.to_dict() for segment in segments[:seg_l + 1]]
        post_segs = [segment.to_dict() for segment in segments[seg_l + 1:]]
    else:
        for idx, segment in enumerate(segments):
            seg_d = segment.to_dict()
            if idx < seg_l:
                pre_segs.append(seg_d)
            elif idx > seg_l:
                post_segs.append(seg_d)
            else:
                words = seg_d.get("words", [])
                pre_w = [word for word in words if (float(word.get("start", 0.0)) + float(word.get("end", 0.0))) / 2.0 < split_time]
                post_w = [word for word in words if (float(word.get("start", 0.0)) + float(word.get("end", 0.0))) / 2.0 >= split_time]

                if pre_w:
                    payload = deepcopy(seg_d)
                    payload["words"], payload["text"] = pre_w, " ".join(
                        [word.get("word", word.get("text", "")).strip() for word in pre_w]
                    ).strip()
                    payload["start"], payload["end"] = seg_d.get("start", pre_w[0].get("start", 0.0)), pre_w[-1].get("end", split_time)
                    if payload["start"] > payload["end"]:
                        payload["start"] = payload["end"]
                    pre_segs.append(payload)
                if post_w:
                    payload = deepcopy(seg_d)
                    payload["words"], payload["text"] = post_w, " ".join(
                        [word.get("word", word.get("text", "")).strip() for word in post_w]
                    ).strip()
                    payload["start"], payload["end"] = post_w[0].get("start", split_time), seg_d.get("end", post_w[-1].get("end", split_time))
                    if payload["start"] > payload["end"]:
                        payload["start"] = payload["end"]
                    post_segs.append(payload)

    return split_time, pre_segs, post_segs


def build_smart_split_outcome(
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

    split_time, pre_segs, post_segs = calculate_smart_split(
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


def apply_smart_split(
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
    utc_now,
) -> bool:
    split_outcome = build_smart_split_outcome(
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
        pre_audio_path = save_to_temp(
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
        session_state.next_transcribe_time = utc_now()
        shared_state.prev_tc_res = split_outcome.post_result

        bc.tc_sentences = _enforce_sentence_limits(bc.tc_sentences, sentence_limitless, max_sentences)
        bc.update_tc(shared_state.prev_tc_res, separator)
        translator.dispatch(pre_audio_path, _build_full_transcribed_text(bc.tc_sentences, shared_state.prev_tc_res))
        return True
    except Exception as exc:
        logger.warning(f"Smart-Split fallback due to error: {exc}")
        return False


def break_buffer_and_update_state(
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
    utc_now,
) -> None:
    logger.info(f"Buffer break [{reason}] | bytes={len(session_state.last_sample)} dur={session_state.duration_seconds:.2f}s")

    preserved_tc = (
        reason == "buffer_full"
        and is_tc
        and bool(shared_state.prev_tc_res)
        and hasattr(shared_state.prev_tc_res, "segments")
        and apply_smart_split(
            session_state=session_state,
            previous_result=shared_state.prev_tc_res,
            sr_divider=sr_divider,
            samp_width=samp_width,
            num_of_channels=num_of_channels,
            sentence_limitless=sentence_limitless,
            max_sentences=max_sentences,
            separator=separator,
            translator=translator,
            utc_now=utc_now,
        )
    )

    if preserved_tc:
        return

    buffer_reducer.reduce_sentences()
    session_state.reset_buffer()
