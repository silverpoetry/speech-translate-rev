from __future__ import annotations

import os
import sys
import unittest
from datetime import timedelta

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.utils.audio.record import (
    BufferStateReducer,
    RealtimeCallbackContext,
    RecordingRuntime,
    RecordingStatusEmitter,
    RealtimeSharedState,
    RealtimeSessionState,
    SmartSplitOutcome,
    TranslationDispatcher,
    TranslationTask,
    _apply_smart_split,
    _break_buffer_and_update_state,
    _consume_record_loop_input,
    _drain_pending_audio,
    _build_recording_sentence_count_text,
    _build_record_audio_target,
    _build_recording_session_config,
    _load_recording_model_runtime,
    _advance_recording_buffer,
    _cleanup_translation_audio,
    _commit_realtime_transcription,
    _calculate_buffer_duration,
    _execute_realtime_transcription,
    _filter_realtime_transcription_result,
    _handle_record_callback_error,
    _initialize_callback_context,
    _merge_translation_units,
    _normalize_translation_result_units,
    _reset_callback_context,
    _resolve_live_input_source_language,
    _prime_realtime_vad,
    _detect_realtime_speech,
    _update_realtime_queue_state,
    _build_smart_split_outcome,
    _build_recording_state_payload,
    _build_full_transcribed_text,
    _result_text,
)


class FakeResult:
    def __init__(self, text: str, language: str = "en") -> None:
        self.text = text
        self.language = language


class FakeSegment:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def to_dict(self) -> dict:
        return dict(self._payload)


class FakeSmartResult:
    def __init__(self, segments) -> None:
        self.segments = [segment if isinstance(segment, FakeSegment) else FakeSegment(segment) for segment in segments]
        self.text = " ".join(seg.to_dict().get("text", "") for seg in self.segments).strip()


class FakeWebBridge:
    def __init__(self) -> None:
        self.messages = []
        self.states = []

    def update_task_message(self, message: str) -> None:
        self.messages.append(message)

    def set_recording_state(self, payload) -> None:
        self.states.append(payload)


class FakeTranslator:
    def __init__(self) -> None:
        self.calls = []

    def dispatch(self, audio_target, text_snapshot: str) -> None:
        self.calls.append((audio_target, text_snapshot))


class FakeBufferReducer:
    def __init__(self) -> None:
        self.calls = 0

    def reduce_sentences(self) -> None:
        self.calls += 1


class FakeLock:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeTensor:
    def __init__(self, value: float = 0.9, size: int = 1024) -> None:
        self._value = value
        self._size = size

    def numel(self) -> int:
        return self._size

    def item(self) -> float:
        return self._value


class AudioRecordHelpersTests(unittest.TestCase):
    def test_result_text_supports_result_object_and_string(self) -> None:
        self.assertEqual(_result_text(FakeResult(" hello ")), "hello")
        self.assertEqual(_result_text(" world "), "world")
        self.assertEqual(_result_text(None), "")

    def test_build_full_transcribed_text_joins_sentence_history(self) -> None:
        sentences = [FakeResult("alpha"), " beta ", FakeResult("   ")]
        combined = _build_full_transcribed_text(sentences, FakeResult("gamma"))
        self.assertEqual(combined, "alpha\nbeta\ngamma")

    def test_shared_state_defaults_are_empty(self) -> None:
        state = RealtimeSharedState()
        self.assertEqual(state.prev_tc_res, "")
        self.assertEqual(state.prev_tl_res, "")
        self.assertIsNone(state.last_db)

    def test_session_state_tracks_buffer_and_duration(self) -> None:
        state = RealtimeSessionState()
        state.append_audio(b"abcd")
        duration = state.recalculate_duration(samp_width=2, num_of_channels=1, sr_divider=2)
        self.assertEqual(state.last_sample, b"abcd")
        self.assertEqual(duration, 1.0)
        state.prev_tc_buffer_seconds = 3.0
        state.reset_buffer()
        self.assertEqual(state.last_sample, b"")
        self.assertEqual(state.duration_seconds, 0.0)
        self.assertEqual(state.prev_tc_buffer_seconds, 0.0)

    def test_initialize_callback_context_captures_runtime_settings(self) -> None:
        ctx = _initialize_callback_context(
            sample_rate=48000,
            chunk_size=960,
            threshold_enable=True,
            threshold_db=-20.0,
            threshold_auto=True,
            use_silero=True,
            silero_min_conf=0.75,
            num_of_channels=2,
            samp_width=2,
            use_temp=False,
            webrtc_vad=object(),
            silero_vad=object(),
        )
        self.assertIsInstance(ctx, RealtimeCallbackContext)
        self.assertEqual(ctx.sample_rate, 48000)
        self.assertEqual(ctx.num_of_channels, 2)
        self.assertGreater(ctx.frame_duration_ms, 0)

    def test_reset_callback_context_clears_global_state(self) -> None:
        from speech_translate.utils.audio import record as record_module

        record_module._initialize_callback_context(
            sample_rate=16000,
            chunk_size=320,
            threshold_enable=True,
            threshold_db=-20.0,
            threshold_auto=True,
            use_silero=True,
            silero_min_conf=0.75,
            num_of_channels=1,
            samp_width=2,
            use_temp=False,
            webrtc_vad=object(),
            silero_vad=object(),
        )
        _reset_callback_context()
        self.assertIsNone(record_module.callback_context)

    def test_translation_task_defaults_are_explicit(self) -> None:
        task = TranslationTask(kind="whisper", separator="<br />")
        self.assertEqual(task.kind, "whisper")
        self.assertEqual(task.separator, "<br />")
        self.assertIsNone(task.audio)
        self.assertFalse(task.cleanup_audio)
        self.assertEqual(task.text, "")

    def test_build_recording_state_payload_omits_optional_fields_when_missing(self) -> None:
        payload = _build_recording_state_payload(
            status="Recording",
            device="mic",
            lang_source="English",
            lang_target="Chinese",
            engine="Google Translate",
            mode="Transcribe & Translate",
        )
        self.assertEqual(
            payload,
            {
                "status": "Recording",
                "device": "mic",
                "lang_source": "English",
                "lang_target": "Chinese",
                "engine": "Google Translate",
                "mode": "Transcribe & Translate",
            },
        )

    def test_build_recording_state_payload_includes_optional_fields(self) -> None:
        payload = _build_recording_state_payload(
            status="Recording",
            device="speaker",
            lang_source="English",
            lang_target="-",
            engine="Whisper",
            mode="Translate",
            timer="00:00:10",
            buffer_text="1.2/10.0 sec",
            sentences="3/5",
        )
        self.assertEqual(payload["timer"], "00:00:10")
        self.assertEqual(payload["buffer"], "1.2/10.0 sec")
        self.assertEqual(payload["sentences"], "3/5")

    def test_build_recording_session_config_reads_runtime_settings(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_cache = dict(record_module.sj.cache)
        try:
            record_module.sj.cache["transcribe_rate"] = 750
            record_module.sj.cache["max_buffer_mic"] = 12
            record_module.sj.cache["max_sentences_mic"] = 7
            record_module.sj.cache["mic_no_limit"] = True
            record_module.sj.cache["threshold_enable_mic"] = False
            record_module.sj.cache["threshold_db_mic"] = -18
            record_module.sj.cache["threshold_auto_mic"] = False
            record_module.sj.cache["threshold_auto_silero_mic"] = False
            record_module.sj.cache["threshold_silero_mic_min"] = 0.6
            record_module.sj.cache["auto_break_buffer_mic"] = False
            record_module.sj.cache["use_temp"] = True
            record_module.sj.cache["separate_with"] = repr(" | ")

            config = _build_recording_session_config(
                rec_type="mic",
                lang_source="Auto Detect",
                engine="Whisper",
                is_tc=True,
                is_tl=True,
            )
        finally:
            record_module.sj.cache.clear()
            record_module.sj.cache.update(previous_cache)

        self.assertEqual(config.transcribe_rate.total_seconds(), 0.75)
        self.assertEqual(config.max_buffer_s, 12)
        self.assertEqual(config.max_sentences, 7)
        self.assertTrue(config.sentence_limitless)
        self.assertFalse(config.threshold_enable)
        self.assertFalse(config.threshold_auto)
        self.assertFalse(config.use_silero)
        self.assertEqual(config.silero_min_conf, 0.6)
        self.assertFalse(config.auto_break_buffer)
        self.assertTrue(config.use_temp)
        self.assertEqual(config.taskname, "Transcribe & Translate")

    def test_load_recording_model_runtime_builds_whisper_runtime(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_get_model_args = record_module.get_model_args
        previous_get_model = record_module.get_model
        previous_get_tc_args = record_module.get_tc_args
        previous_get_filter = record_module.get_hallucination_filter
        previous_cache = dict(record_module.sj.cache)
        try:
            record_module.sj.cache["enable_initial_prompt"] = False
            record_module.sj.cache["use_faster_whisper"] = True
            record_module.sj.cache["filter_rec"] = True
            record_module.sj.cache["path_filter_rec"] = "filters.txt"

            record_module.get_model_args = lambda cache: {"device": "cpu"}
            record_module.get_model = lambda *args, **kwargs: (None, None, lambda *a, **k: FakeResult("tc"), lambda *a, **k: FakeResult("tl"), {"foo": "bar"})
            record_module.get_tc_args = lambda to_args, cache: {"demucs": True, "vad": True}
            record_module.get_hallucination_filter = lambda *args, **kwargs: {"english": ["x"]}

            config = _build_recording_session_config(
                rec_type="mic",
                lang_source="English",
                engine="Whisper",
                is_tc=True,
                is_tl=True,
            )
            runtime = _load_recording_model_runtime(
                config=config,
                lang_source="English",
                model_name_tc="base",
                engine="Whisper",
                is_tc=True,
                is_tl=True,
            )
        finally:
            record_module.get_model_args = previous_get_model_args
            record_module.get_model = previous_get_model
            record_module.get_tc_args = previous_get_tc_args
            record_module.get_hallucination_filter = previous_get_filter
            record_module.sj.cache.clear()
            record_module.sj.cache.update(previous_cache)

        self.assertTrue(runtime.use_temp)
        self.assertTrue(runtime.demucs_enabled)
        self.assertEqual(runtime.cuda_device, "cpu")
        self.assertEqual(runtime.hallucination_filters, {"english": ["x"]})
        self.assertEqual(runtime.whisper_args["language"], "en")

    def test_resolve_live_input_source_language_prefers_detected_supported_language(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_auto_lang = record_module.bc.auto_detected_lang
        try:
            record_module.bc.auto_detected_lang = "en"
            resolved = _resolve_live_input_source_language("Auto Detect", "Google Translate")
        finally:
            record_module.bc.auto_detected_lang = previous_auto_lang

        self.assertEqual(resolved, "english")

    def test_normalize_translation_result_units_aligns_with_source_units(self) -> None:
        aligned = _normalize_translation_result_units([" one ", "", "three"], ["a", "b", "c"])
        self.assertEqual(aligned, ["one", "three"])

    def test_merge_translation_units_preserves_sentence_spacing_rules(self) -> None:
        merged = _merge_translation_units(["hello", "world", "!"])
        self.assertEqual(merged, ["hello world", "!"])

    def test_build_recording_sentence_count_text_includes_limit_when_enabled(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_tc = list(record_module.bc.tc_sentences)
        previous_tl = list(record_module.bc.tl_sentences)
        try:
            record_module.bc.tc_sentences = ["a", "b"]
            record_module.bc.tl_sentences = []
            count_text = _build_recording_sentence_count_text(sentence_limitless=False, max_sentences=5)
        finally:
            record_module.bc.tc_sentences = previous_tc
            record_module.bc.tl_sentences = previous_tl

        self.assertEqual(count_text, "2/5")

    def test_cleanup_translation_audio_removes_temp_file(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_remove = record_module.os.remove
        removed = []
        try:
            record_module.os.remove = lambda path: removed.append(path)
            _cleanup_translation_audio("temp.wav")
        finally:
            record_module.os.remove = previous_remove

        self.assertEqual(removed, ["temp.wav"])

    def test_drain_pending_audio_appends_all_buffered_chunks(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_queue = record_module.bc.data_queue
        try:
            record_module.bc.data_queue = record_module.Queue()
            record_module.bc.data_queue.put(b"ab")
            record_module.bc.data_queue.put(b"cd")
            state = RealtimeSessionState(last_sample=b"")
            _drain_pending_audio(state)
        finally:
            record_module.bc.data_queue = previous_queue

        self.assertEqual(state.last_sample, b"abcd")

    def test_advance_recording_buffer_waits_for_next_transcribe_time(self) -> None:
        state = RealtimeSessionState(last_sample=b"", next_transcribe_time=None)
        ready = _advance_recording_buffer(
            state,
            b"ab",
            transcribe_rate=timedelta(seconds=10),
            samp_width=1,
            num_of_channels=1,
            sr_divider=10,
            min_input_length=0.1,
        )
        self.assertFalse(ready)
        self.assertEqual(state.last_sample, b"ab")

    def test_calculate_buffer_duration_handles_invalid_denominator(self) -> None:
        self.assertEqual(
            _calculate_buffer_duration(b"abcd", samp_width=0, num_of_channels=1, sr_divider=16000),
            0.0,
        )

    def test_execute_realtime_transcription_uses_lock_when_present(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_lock = record_module.bc.tc_lock
        calls = []

        def stable_tc(audio_target, **kwargs):
            calls.append((audio_target, kwargs["task"]))
            return FakeResult("ok")

        try:
            record_module.bc.tc_lock = FakeLock()
            result = _execute_realtime_transcription("audio", stable_tc, {"beam_size": 5})
        finally:
            record_module.bc.tc_lock = previous_lock

        self.assertEqual(result.text, "ok")
        self.assertEqual(calls, [("audio", "transcribe")])

    def test_filter_realtime_transcription_result_uses_configured_language(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_remove = record_module.remove_segments_by_str
        previous_filter_rec = record_module.sj.cache["filter_rec"]
        captured = {}

        def fake_remove(result, filters, *args):
            captured["filters"] = filters
            return result

        try:
            record_module.sj.cache["filter_rec"] = True
            record_module.remove_segments_by_str = fake_remove
            filtered = _filter_realtime_transcription_result(
                FakeResult("hello", language="en"),
                hallucination_filters={"english": ["x"]},
                auto=False,
                configured_language="english",
            )
        finally:
            record_module.remove_segments_by_str = previous_remove
            record_module.sj.cache["filter_rec"] = previous_filter_rec

        self.assertIsNotNone(filtered)
        self.assertEqual(captured["filters"], ["x"])

    def test_filter_realtime_transcription_result_uses_detected_language_when_auto(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_remove = record_module.remove_segments_by_str
        previous_filter_rec = record_module.sj.cache["filter_rec"]
        captured = {}

        def fake_remove(result, filters, *args):
            captured["filters"] = filters
            return result

        try:
            record_module.sj.cache["filter_rec"] = True
            record_module.remove_segments_by_str = fake_remove
            filtered = _filter_realtime_transcription_result(
                FakeResult("hello", language="en"),
                hallucination_filters={"english": ["y"]},
                auto=True,
                configured_language=None,
            )
        finally:
            record_module.remove_segments_by_str = previous_remove
            record_module.sj.cache["filter_rec"] = previous_filter_rec

        self.assertIsNotNone(filtered)
        self.assertEqual(captured["filters"], ["y"])

    def test_commit_realtime_transcription_updates_state_and_dispatches(self) -> None:
        from speech_translate.utils.audio import record as record_module

        translator = FakeTranslator()
        previous_prev_tc = record_module.shared_state.prev_tc_res
        previous_auto_lang = record_module.bc.auto_detected_lang
        previous_tc_sentences = list(record_module.bc.tc_sentences)
        previous_update_tc = getattr(record_module.bc, "update_tc", None)
        previous_status = record_module.bc.current_rec_status
        tc_updates = []
        try:
            record_module.shared_state.prev_tc_res = ""
            record_module.bc.auto_detected_lang = "~"
            record_module.bc.tc_sentences = []
            record_module.bc.current_rec_status = "busy"
            record_module.bc.update_tc = lambda result, separator: tc_updates.append((result, separator))

            _commit_realtime_transcription(
                FakeResult("hello", language="en"),
                audio_target="audio",
                is_tl=True,
                separator="<br />",
                translator=translator,
            )
        finally:
            record_module.shared_state.prev_tc_res = previous_prev_tc
            record_module.bc.auto_detected_lang = previous_auto_lang
            record_module.bc.tc_sentences = previous_tc_sentences
            record_module.bc.current_rec_status = previous_status
            if previous_update_tc is not None:
                record_module.bc.update_tc = previous_update_tc

        self.assertEqual(tc_updates[-1][1], "<br />")
        self.assertEqual(translator.calls[-1], ("audio", "hello"))

    def test_build_record_audio_target_tracks_temp_file(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_save = record_module._save_to_temp
        session_state = RealtimeSessionState(last_sample=b"abc")
        try:
            record_module._save_to_temp = lambda *args, **kwargs: "temp.wav"
            audio_target = _build_record_audio_target(
                session_state,
                use_temp=True,
                num_of_channels=1,
                samp_width=2,
                demucs_enabled=False,
                cuda_device="cpu",
                sr_ori=16000,
            )
        finally:
            record_module._save_to_temp = previous_save

        self.assertEqual(audio_target, "temp.wav")
        self.assertEqual(session_state.temp_audio_paths, ["temp.wav"])

    def test_prime_realtime_vad_marks_context_checked(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_get_speech = record_module.get_speech_webrtc
        previous_to_silero = record_module.to_silero
        calls = []
        ctx = RealtimeCallbackContext(
            sample_rate=16000,
            frame_duration_ms=30,
            threshold_enable=True,
            threshold_db=-20.0,
            threshold_auto=True,
            use_silero=True,
            silero_min_conf=0.75,
            vad_checked=False,
            num_of_channels=1,
            samp_width=2,
            use_temp=False,
            webrtc_vad=object(),
            silero_vad=lambda *args: calls.append("silero"),
        )
        try:
            record_module.get_speech_webrtc = lambda *args, **kwargs: calls.append("webrtc")
            record_module.to_silero = lambda *args, **kwargs: FakeTensor()
            _prime_realtime_vad(ctx, b"abcd")
        finally:
            record_module.get_speech_webrtc = previous_get_speech
            record_module.to_silero = previous_to_silero

        self.assertTrue(ctx.vad_checked)
        self.assertEqual(calls, ["webrtc", "silero"])

    def test_detect_realtime_speech_uses_manual_threshold(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_get_db = record_module.get_db
        ctx = RealtimeCallbackContext(
            sample_rate=16000,
            frame_duration_ms=30,
            threshold_enable=True,
            threshold_db=-30.0,
            threshold_auto=False,
            use_silero=False,
            silero_min_conf=0.75,
            vad_checked=True,
            num_of_channels=1,
            samp_width=2,
            use_temp=False,
        )
        try:
            record_module.get_db = lambda _: -10.0
            is_speech, payload = _detect_realtime_speech(ctx, b"orig", b"resampled")
        finally:
            record_module.get_db = previous_get_db

        self.assertTrue(is_speech)
        self.assertEqual(payload, b"resampled")

    def test_update_realtime_queue_state_tracks_silence_edges(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_queue = record_module.bc.data_queue
        previous_status = record_module.bc.current_rec_status
        try:
            record_module.bc.data_queue = record_module.Queue()
            record_module.bc.current_rec_status = "busy"
            ctx = RealtimeCallbackContext(
                sample_rate=16000,
                frame_duration_ms=30,
                threshold_enable=True,
                threshold_db=-20.0,
                threshold_auto=False,
                use_silero=False,
                silero_min_conf=0.75,
                vad_checked=True,
                num_of_channels=1,
                samp_width=2,
                use_temp=False,
            )
            _update_realtime_queue_state(ctx, is_speech=True, data_to_queue=b"abc")
            queued = record_module.bc.data_queue.get_nowait()
            _update_realtime_queue_state(ctx, is_speech=False, data_to_queue=b"")
        finally:
            record_module.bc.data_queue = previous_queue
            record_module.bc.current_rec_status = previous_status

        self.assertEqual(queued, b"abc")
        self.assertTrue(ctx.is_silence)

    def test_handle_record_callback_error_downgrades_auto_threshold(self) -> None:
        ctx = RealtimeCallbackContext(
            sample_rate=16000,
            frame_duration_ms=20,
            threshold_enable=True,
            threshold_db=-20.0,
            threshold_auto=True,
            use_silero=False,
            silero_min_conf=0.75,
            vad_checked=True,
            num_of_channels=1,
            samp_width=2,
            use_temp=False,
        )
        _handle_record_callback_error(ctx, Exception("Error while processing frame"))
        self.assertEqual(ctx.frame_duration_ms, 10)
        self.assertFalse(ctx.vad_checked)

    def test_translation_dispatcher_queues_whisper_task(self) -> None:
        updates = []
        dispatcher = TranslationDispatcher(
            is_tl=True,
            tl_engine_whisper=True,
            use_temp=True,
            keep_temp=False,
            separator="<br />",
            lang_source="English",
            lang_target="Chinese",
            engine="Whisper",
            hallucination_filters={},
            stable_tl=object(),
            whisper_args={},
            record_status_updater=lambda: updates.append("u"),
        )
        dispatcher.dispatch("temp.wav", "")
        task = dispatcher._queue.get_nowait()
        self.assertEqual(task.kind, "whisper")
        self.assertEqual(task.audio, "temp.wav")
        self.assertTrue(task.cleanup_audio)

    def test_translation_dispatcher_replaces_duplicate_api_task_by_text(self) -> None:
        dispatcher = TranslationDispatcher(
            is_tl=True,
            tl_engine_whisper=False,
            use_temp=False,
            keep_temp=False,
            separator="<br />",
            lang_source="English",
            lang_target="Chinese",
            engine="Google Translate",
            hallucination_filters={},
            stable_tl=object(),
            whisper_args={},
            record_status_updater=lambda: None,
        )
        dispatcher.dispatch(None, "hello")
        first_task = dispatcher._latest_api_task
        dispatcher.dispatch(None, "hello")
        self.assertIsNotNone(first_task)
        self.assertEqual(dispatcher._latest_api_task.text, first_task.text)
        self.assertEqual(dispatcher._latest_api_task.lang_target, first_task.lang_target)
        dispatcher.dispatch(None, "world")
        self.assertEqual(dispatcher._latest_api_task.text, "world")

    def test_recording_status_emitter_updates_bridge(self) -> None:
        runtime = RecordingRuntime(
            taskname="Transcribe",
            device="mic",
            lang_source="English",
            lang_target="Chinese",
            engine="Whisper",
            is_tl=True,
            use_temp=False,
            separator="<br />",
            keep_temp=False,
            t_start=0.0,
            max_buffer_s=10.0,
            max_sentences=5,
            sentence_limitless=False,
            lang_target_display="Chinese",
        )
        bridge = FakeWebBridge()
        from speech_translate.utils.audio import record as record_module

        previous_bridge = record_module.bc.web_bridge
        try:
            record_module.bc.web_bridge = bridge
            emitter = RecordingStatusEmitter(runtime)
            emitter.emit(status="Recording", timer="00:00:01", buffer_text="1.0/10.0 sec", sentences="2/5")
        finally:
            record_module.bc.web_bridge = previous_bridge

        self.assertEqual(bridge.messages, ["Recording"])
        self.assertEqual(bridge.states[-1]["status"], "Recording")
        self.assertEqual(bridge.states[-1]["timer"], "00:00:01")

    def test_buffer_state_reducer_moves_previous_results_into_sentences(self) -> None:
        from speech_translate.utils.audio import record as record_module

        translator = FakeTranslator()
        reducer = BufferStateReducer(
            is_tc=True,
            is_tl=True,
            tl_engine_whisper=True,
            sentence_limitless=False,
            max_sentences=2,
            separator="<br />",
            translator=translator,
        )

        previous_tc_sentences = list(record_module.bc.tc_sentences)
        previous_tl_sentences = list(record_module.bc.tl_sentences)
        previous_prev_tc = record_module.shared_state.prev_tc_res
        previous_prev_tl = record_module.shared_state.prev_tl_res
        previous_update_tc = getattr(record_module.bc, "update_tc", None)
        previous_update_tl = getattr(record_module.bc, "update_tl", None)

        tc_updates = []
        tl_updates = []
        try:
            record_module.bc.tc_sentences = ["old"]
            record_module.bc.tl_sentences = []
            record_module.shared_state.prev_tc_res = FakeResult("new")
            record_module.shared_state.prev_tl_res = FakeResult("translated")
            record_module.bc.update_tc = lambda result, separator: tc_updates.append((result, separator))
            record_module.bc.update_tl = lambda result, separator: tl_updates.append((result, separator))

            reducer.reduce_sentences()
        finally:
            record_module.bc.tc_sentences = previous_tc_sentences
            record_module.bc.tl_sentences = previous_tl_sentences
            record_module.shared_state.prev_tc_res = previous_prev_tc
            record_module.shared_state.prev_tl_res = previous_prev_tl
            if previous_update_tc is not None:
                record_module.bc.update_tc = previous_update_tc
            if previous_update_tl is not None:
                record_module.bc.update_tl = previous_update_tl

        self.assertEqual(tc_updates[-1][1], "<br />")
        self.assertEqual(tl_updates[-1][1], "<br />")
        self.assertEqual(translator.calls[-1], (None, "old\nnew"))

    def test_build_smart_split_outcome_slices_audio_and_results(self) -> None:
        result = FakeSmartResult(
            [
                {
                    "text": "alpha",
                    "words": [
                        {"word": "alpha", "start": 5.0, "end": 6.0},
                    ],
                },
                {
                    "text": "beta",
                    "words": [
                        {"word": "beta", "start": 9.0, "end": 10.0},
                    ],
                },
            ]
        )
        audio = b"0123456789ABCDEFGHIJ"
        outcome = _build_smart_split_outcome(
            result,
            audio,
            prev_buffer_seconds=8.0,
            sr_divider=1,
            samp_width=1,
            num_of_channels=1,
        )
        self.assertIsInstance(outcome, SmartSplitOutcome)
        self.assertEqual(outcome.pre_audio_bytes, b"01234567")
        self.assertEqual(outcome.post_audio_bytes, b"89ABCDEFGHIJ")

    def test_apply_smart_split_updates_session_and_dispatches(self) -> None:
        from speech_translate.utils.audio import record as record_module

        result = FakeSmartResult(
            [
                {
                    "text": "alpha",
                    "words": [{"word": "alpha", "start": 5.0, "end": 6.0}],
                },
                {
                    "text": "beta",
                    "words": [{"word": "beta", "start": 9.0, "end": 10.0}],
                },
            ]
        )
        session_state = RealtimeSessionState(last_sample=b"0123456789ABCDEFGHIJ", duration_seconds=20.0)
        translator = FakeTranslator()

        previous_tc_sentences = list(record_module.bc.tc_sentences)
        previous_prev_tc = record_module.shared_state.prev_tc_res
        previous_update_tc = getattr(record_module.bc, "update_tc", None)
        tc_updates = []
        try:
            record_module.bc.tc_sentences = []
            record_module.shared_state.prev_tc_res = result
            record_module.bc.update_tc = lambda current, separator: tc_updates.append((current, separator))
            session_state.prev_tc_buffer_seconds = 8.0

            applied = _apply_smart_split(
                session_state=session_state,
                previous_result=result,
                sr_divider=1,
                samp_width=1,
                num_of_channels=1,
                sentence_limitless=False,
                max_sentences=5,
                separator="<br />",
                translator=translator,
            )
        finally:
            record_module.bc.tc_sentences = previous_tc_sentences
            record_module.shared_state.prev_tc_res = previous_prev_tc
            if previous_update_tc is not None:
                record_module.bc.update_tc = previous_update_tc

        self.assertTrue(applied)
        self.assertEqual(session_state.last_sample, b"89ABCDEFGHIJ")
        self.assertEqual(session_state.duration_seconds, 12.0)
        self.assertIsNotNone(session_state.next_transcribe_time)
        self.assertEqual(tc_updates[-1][1], "<br />")
        self.assertEqual(translator.calls[-1][1], "alpha\nbeta")

    def test_break_buffer_falls_back_to_reducer_when_split_not_preserved(self) -> None:
        from speech_translate.utils.audio import record as record_module

        session_state = RealtimeSessionState(last_sample=b"1234", duration_seconds=2.0)
        translator = FakeTranslator()
        reducer = FakeBufferReducer()
        previous_prev_tc = record_module.shared_state.prev_tc_res
        try:
            record_module.shared_state.prev_tc_res = ""
            _break_buffer_and_update_state(
                reason="silence",
                session_state=session_state,
                is_tc=True,
                sr_divider=1,
                samp_width=1,
                num_of_channels=1,
                sentence_limitless=False,
                max_sentences=5,
                separator="<br />",
                translator=translator,
                buffer_reducer=reducer,
            )
        finally:
            record_module.shared_state.prev_tc_res = previous_prev_tc

        self.assertEqual(reducer.calls, 1)
        self.assertEqual(session_state.last_sample, b"")
        self.assertEqual(session_state.duration_seconds, 0.0)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
