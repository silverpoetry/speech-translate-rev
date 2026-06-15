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
    RecordingSessionDependencies,
    RecordingSessionFinalizeContext,
    RecordingSessionLifecycle,
    RecordingSessionRequest,
    RecordingSessionServices,
    RecordingStreamRuntime,
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
    _prepare_recording_session_bootstrap,
    build_recording_session_control,
    _build_recording_session_config,
    _load_recording_model_runtime,
    _advance_recording_buffer,
    _cleanup_processed_audio_target,
    _cleanup_translation_audio,
    _commit_realtime_transcription,
    _build_recording_session_services,
    _build_recording_stream_runtime,
    _initialize_recording_session_lifecycle,
    _execute_recording_iteration,
    _calculate_buffer_duration,
    _execute_realtime_transcription,
    _filter_realtime_transcription_result,
    _handle_record_callback_error,
    _initialize_callback_context,
    _run_recording_session_loop,
    _merge_translation_units,
    _normalize_translation_result_units,
    _open_recording_stream,
    _reset_callback_context,
    _resolve_live_input_source_language,
    _start_recording_session_support_threads,
    _prime_realtime_vad,
    _detect_realtime_speech,
    _update_realtime_queue_state,
    _build_smart_split_outcome,
    _build_recording_state_payload,
    _build_full_transcribed_text,
    _result_text,
    build_recording_text_state,
    record_session,
)
from speech_translate.bridge_runtime_state import BridgeLiveTextRuntime, BridgeRecordingRuntime
from speech_translate.runtime_registry import bridge_state_registry
from speech_translate.utils.audio.record_settings import (
    build_recording_model_settings,
    build_recording_stream_settings,
)
from speech_translate.utils.translate.translation_runtime_settings import build_realtime_translation_settings
from speech_translate.utils.audio.recording_runtime_state import (
    build_recording_runtime_state_adapter,
    build_recording_text_store_adapter,
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


class FakeRuntimeTextState:
    def __init__(
        self,
        *,
        tc_sentences=None,
        tl_sentences=None,
        detected_language: str = "~",
        prev_tc_res="",
        prev_tl_res="",
    ) -> None:
        self._tc_sentences = [] if tc_sentences is None else list(tc_sentences)
        self._tl_sentences = [] if tl_sentences is None else list(tl_sentences)
        self._detected_language = detected_language
        self._prev_tc_res = prev_tc_res
        self._prev_tl_res = prev_tl_res
        self.tc_updates = []
        self.tl_updates = []

    def transcribed_sentences(self):
        return list(self._tc_sentences)

    def translated_sentences(self):
        return list(self._tl_sentences)

    def set_transcribed_sentences(self, sentences):
        self._tc_sentences = list(sentences)

    def set_translated_sentences(self, sentences):
        self._tl_sentences = list(sentences)

    def update_transcribed_output(self, current, separator):
        self.tc_updates.append((current, separator))

    def update_translated_output(self, current, separator):
        self.tl_updates.append((current, separator))

    def detected_language(self) -> str:
        return self._detected_language

    def set_detected_language(self, language: str) -> None:
        self._detected_language = language

    def previous_transcribed_result(self):
        return self._prev_tc_res

    def previous_translated_result(self):
        return self._prev_tl_res

    def set_previous_transcribed_result(self, result):
        self._prev_tc_res = result

    def set_previous_translated_result(self, result):
        self._prev_tl_res = result


class FakeRecordingSessionControl:
    def __init__(self, *, recording: bool = True, status: str = "Recording", queue_items=None, stream=None) -> None:
        from queue import Queue

        self._recording = recording
        self._status = status
        self._queue = Queue()
        for item in queue_items or []:
            self._queue.put(item)
        self._stream = stream
        self.runtime_threads_cleared = False

    def is_recording(self) -> bool:
        return self._recording

    def set_recording(self, value: bool) -> None:
        self._recording = value

    def current_status(self) -> str:
        return self._status

    def set_current_status(self, status: str) -> None:
        self._status = status

    def data_queue_empty(self) -> bool:
        return self._queue.empty()

    def get_data(self, *, timeout: float):
        return self._queue.get(timeout=timeout)

    def get_data_nowait(self):
        return self._queue.get_nowait()

    def clear_data_queue(self) -> None:
        while not self._queue.empty():
            self._queue.get_nowait()

    def stream(self):
        return self._stream

    def clear_stream(self) -> None:
        self._stream = None

    def clear_runtime_threads(self) -> None:
        self.runtime_threads_cleared = True


class FakeCallbackContextStore:
    def __init__(self) -> None:
        self.value = None

    def get(self):
        return self.value

    def set(self, context):
        self.value = context
        return context

    def reset(self) -> None:
        self.value = None


class FakeStreamingStateAdapter:
    def __init__(self) -> None:
        self.stream = None
        self.queued = []
        self.statuses = []

    def set_stream(self, stream) -> None:
        self.stream = stream

    def enqueue_audio(self, payload: bytes) -> None:
        self.queued.append(payload)

    def set_current_status(self, status: str) -> None:
        self.statuses.append(status)

    def current_status(self) -> str:
        return self.statuses[-1] if self.statuses else ""


class FakeSettingsStore:
    def __init__(self, cache: dict[str, object]) -> None:
        self.cache = dict(cache)


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
    def test_recording_runtime_state_default_provider_reads_bridge_substates(self) -> None:
        from speech_translate.utils.audio.recording_runtime_state import RecordingRuntimeStateAdapter, RecordingTextStoreAdapter

        fake_bridge = type(
            "FakeBridgeState",
            (),
            {
                "recording_runtime": BridgeRecordingRuntime(recording=True, current_rec_status="busy"),
                "live_text": BridgeLiveTextRuntime(auto_detected_lang="ja", tc_sentences=["a"], tl_sentences=["b"]),
            },
        )()
        with bridge_state_registry.override(fake_bridge):

            runtime_state = RecordingRuntimeStateAdapter()
            text_store = RecordingTextStoreAdapter()
            self.assertTrue(runtime_state.is_recording_active())
            self.assertEqual(runtime_state.current_status(), "busy")
            self.assertEqual(text_store.detected_language(), "ja")
            self.assertEqual(text_store.transcribed_sentences(), ["a"])
            self.assertEqual(text_store.translated_sentences(), ["b"])

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

    def test_default_recording_text_state_owns_isolated_shared_runtime_state(self) -> None:
        first = build_recording_text_state()
        second = build_recording_text_state()

        first.set_previous_transcribed_result("alpha")

        self.assertEqual(_result_text(first.previous_transcribed_result()), "alpha")
        self.assertEqual(second.previous_transcribed_result(), "")
        self.assertIsNot(first._shared, second._shared)

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
        self.assertIsInstance(ctx.shared_runtime_state, RealtimeSharedState)

    def test_initialize_callback_context_supports_injected_store(self) -> None:
        from speech_translate.utils.audio import record_streaming as streaming_module

        store = FakeCallbackContextStore()
        ctx = streaming_module.initialize_callback_context(
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
            store=store,
        )

        self.assertIs(store.get(), ctx)
        self.assertIs(streaming_module.get_callback_context(store), ctx)

    def test_initialize_callback_context_store_is_isolated_from_default_store(self) -> None:
        from speech_translate.utils.audio import record_streaming as streaming_module

        previous_default_context = streaming_module.get_callback_context()
        store = FakeCallbackContextStore()
        try:
            ctx = streaming_module.initialize_callback_context(
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
                store=store,
            )
            observed_default_context = streaming_module.get_callback_context()
        finally:
            streaming_module.reset_callback_context()
            if previous_default_context is not None:
                streaming_module.callback_context_store.set(previous_default_context)

        self.assertIs(store.get(), ctx)
        self.assertIs(observed_default_context, previous_default_context)

    def test_reset_callback_context_clears_global_state(self) -> None:
        from speech_translate.utils.audio import record as record_module
        from speech_translate.utils.audio import record_streaming as streaming_module

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
        self.assertIsNone(streaming_module.get_callback_context())

    def test_reset_callback_context_supports_injected_store(self) -> None:
        from speech_translate.utils.audio import record_streaming as streaming_module

        store = FakeCallbackContextStore()
        streaming_module.initialize_callback_context(
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
            store=store,
        )
        streaming_module.reset_callback_context(store)
        self.assertIsNone(store.get())

    def test_build_record_callback_uses_provided_context_and_state_adapter(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_get_pyaudio_module = record_module.get_pyaudio_module
        previous_resample_sr = record_module.resample_sr
        previous_detect = record_module._detect_realtime_speech
        previous_update = record_module._update_realtime_queue_state
        observed = {}
        ctx = RealtimeCallbackContext(
            sample_rate=16000,
            frame_duration_ms=20,
            threshold_enable=True,
            threshold_db=-20.0,
            threshold_auto=True,
            use_silero=True,
            silero_min_conf=0.75,
            vad_checked=False,
            num_of_channels=1,
            samp_width=2,
            use_temp=False,
        )
        state_adapter = FakeStreamingStateAdapter()
        try:
            record_module.get_pyaudio_module = lambda: type("FakePyAudioModule", (), {"paContinue": "continue"})()
            record_module.resample_sr = lambda in_data, sample_rate, target_rate: b"resampled"
            record_module._detect_realtime_speech = lambda current_ctx, in_data, resampled: (
                current_ctx is ctx,
                b"queued",
            )

            def fake_update(current_ctx, **kwargs):
                observed["ctx"] = current_ctx
                observed["kwargs"] = kwargs

            record_module._update_realtime_queue_state = fake_update
            callback = record_module.build_record_callback(ctx, state_adapter=state_adapter)
            result = callback(b"raw", 0, None, None)
        finally:
            record_module.get_pyaudio_module = previous_get_pyaudio_module
            record_module.resample_sr = previous_resample_sr
            record_module._detect_realtime_speech = previous_detect
            record_module._update_realtime_queue_state = previous_update

        self.assertEqual(result, (b"raw", "continue"))
        self.assertIs(observed["ctx"], ctx)
        self.assertTrue(observed["kwargs"]["is_speech"])
        self.assertEqual(observed["kwargs"]["data_to_queue"], b"queued")
        self.assertIs(observed["kwargs"]["state_adapter"], state_adapter)

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
        settings_snapshot = {
            "transcribe_rate": 750,
            "max_buffer_mic": 12,
            "max_sentences_mic": 7,
            "mic_no_limit": True,
            "min_input_length_mic": 0.4,
            "keep_temp": False,
            "threshold_enable_mic": False,
            "threshold_db_mic": -18,
            "threshold_auto_mic": False,
            "threshold_auto_silero_mic": False,
            "threshold_silero_mic_min": 0.6,
            "auto_break_buffer_mic": False,
            "use_temp": True,
            "separate_with": repr(" | "),
        }
        config = _build_recording_session_config(
            rec_type="mic",
            lang_source="Auto Detect",
            engine="Whisper",
            is_tc=True,
            is_tl=True,
            settings_snapshot=settings_snapshot,
        )

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

    def test_build_recording_model_settings_extracts_runtime_policy_fields(self) -> None:
        settings = build_recording_model_settings(
            {
                "enable_initial_prompt": True,
                "initial_prompts_map": {"en": "hello"},
                "use_faster_whisper": True,
                "filter_rec": True,
                "path_filter_rec": "filters.txt",
            }
        )

        self.assertTrue(settings.enable_initial_prompt)
        self.assertEqual(settings.initial_prompts_map, {"en": "hello"})
        self.assertTrue(settings.use_faster_whisper)
        self.assertTrue(settings.filter_rec)
        self.assertEqual(settings.path_filter_rec, "filters.txt")

    def test_build_recording_stream_settings_extracts_device_and_vad_policy(self) -> None:
        settings = build_recording_stream_settings(
            rec_type="speaker",
            settings_snapshot={
                "threshold_auto_mode_speaker": "2",
                "supress_record_warning": True,
                "speaker": "[ID: 0,1] | Loopback",
                "chunk_size_speaker": 1024,
                "auto_sample_rate_speaker": True,
                "sample_rate_speaker": 48000,
                "auto_channels_speaker": False,
                "channels_speaker": "2",
            },
        )

        self.assertEqual(settings.threshold_auto_mode, 2)
        self.assertTrue(settings.suppress_record_warning)
        self.assertEqual(settings.device_settings.cache["speaker"], "[ID: 0,1] | Loopback")

    def test_build_realtime_translation_settings_extracts_translation_runtime_policy(self) -> None:
        settings = build_realtime_translation_settings(
            {
                "http_proxy": "http://127.0.0.1:8080",
                "https_proxy": "https://127.0.0.1:8443",
                "libre_link": "http://127.0.0.1:5000",
                "libre_api_key": "secret",
                "filter_rec": True,
                "filter_rec_case_sensitive": True,
                "filter_rec_strip": False,
                "filter_rec_ignore_punctuations": "!?",
                "filter_rec_exact_match": True,
                "filter_rec_similarity": 0.9,
            }
        )

        self.assertEqual(settings.http_proxy, "http://127.0.0.1:8080")
        self.assertEqual(settings.https_proxy, "https://127.0.0.1:8443")
        self.assertEqual(settings.libre_link, "http://127.0.0.1:5000")
        self.assertEqual(settings.libre_api_key, "secret")
        self.assertTrue(settings.filter_rec)
        self.assertTrue(settings.filter_rec_case_sensitive)
        self.assertFalse(settings.filter_rec_strip)
        self.assertEqual(settings.filter_rec_ignore_punctuations, "!?")
        self.assertTrue(settings.filter_rec_exact_match)
        self.assertEqual(settings.filter_rec_similarity, 0.9)

    def test_load_recording_model_runtime_builds_whisper_runtime(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_get_model_args = record_module.get_model_args
        previous_get_model = record_module.get_model
        previous_get_tc_args = record_module.get_tc_args
        previous_get_filter = record_module.get_hallucination_filter
        try:
            record_module.get_model_args = lambda cache: {"device": "cpu"}
            record_module.get_model = lambda *args, **kwargs: (None, None, lambda *a, **k: FakeResult("tc"), lambda *a, **k: FakeResult("tl"), {"foo": "bar"})
            record_module.get_tc_args = lambda to_args, cache: {"demucs": True, "vad": True}
            record_module.get_hallucination_filter = lambda *args, **kwargs: {"english": ["x"]}
            settings_snapshot = {
                "enable_initial_prompt": False,
                "use_faster_whisper": True,
                "filter_rec": True,
                "path_filter_rec": "filters.txt",
            }

            config = _build_recording_session_config(
                rec_type="mic",
                lang_source="English",
                engine="Whisper",
                is_tc=True,
                is_tl=True,
                settings_snapshot={
                    "transcribe_rate": 1000,
                    "max_buffer_mic": 10,
                    "max_sentences_mic": 5,
                    "mic_no_limit": False,
                    "min_input_length_mic": 0.4,
                    "keep_temp": False,
                    "threshold_enable_mic": True,
                    "threshold_db_mic": -20,
                    "threshold_auto_mic": True,
                    "threshold_auto_silero_mic": True,
                    "threshold_silero_mic_min": 0.75,
                    "auto_break_buffer_mic": True,
                    "use_temp": False,
                    "separate_with": repr("\n"),
                },
            )
            runtime = _load_recording_model_runtime(
                config=config,
                lang_source="English",
                model_name_tc="base",
                engine="Whisper",
                is_tc=True,
                is_tl=True,
                settings_snapshot=settings_snapshot,
            )
        finally:
            record_module.get_model_args = previous_get_model_args
            record_module.get_model = previous_get_model
            record_module.get_tc_args = previous_get_tc_args
            record_module.get_hallucination_filter = previous_get_filter

        self.assertTrue(runtime.use_temp)
        self.assertTrue(runtime.demucs_enabled)
        self.assertEqual(runtime.cuda_device, "cpu")
        self.assertEqual(runtime.hallucination_filters, {"english": ["x"]})
        self.assertEqual(runtime.whisper_args["language"], "en")

    def test_prepare_recording_session_bootstrap_snapshots_runtime_sequence(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_build_config = record_module._build_recording_session_config
        previous_load_runtime = record_module._load_recording_model_runtime
        previous_build_stream = record_module._build_recording_stream_runtime
        calls = []
        try:
            config = type("Config", (), {"use_temp": False, "taskname": "Transcribe"})()
            model_runtime = type("ModelRuntime", (), {"use_temp": True, "cuda_device": "cpu", "demucs_enabled": False})()
            stream_runtime = RecordingStreamRuntime(
                input_device_index=0,
                sr_ori=16000,
                num_of_channels=1,
                chunk_size=320,
                samp_width=2,
                sr_divider=16000,
                callback_ctx=RealtimeCallbackContext(
                    sample_rate=16000,
                    frame_duration_ms=20,
                    threshold_enable=True,
                    threshold_db=-20.0,
                    threshold_auto=True,
                    use_silero=True,
                    silero_min_conf=0.75,
                    vad_checked=False,
                    num_of_channels=1,
                    samp_width=2,
                    use_temp=True,
                ),
            )

            record_module._build_recording_session_config = lambda **kwargs: calls.append(("config", kwargs["settings_snapshot"]["use_temp"])) or config
            record_module._load_recording_model_runtime = lambda **kwargs: calls.append(("model", kwargs["config"].use_temp)) or model_runtime
            record_module._build_recording_stream_runtime = (
                lambda **kwargs: calls.append(("stream", kwargs["config"].use_temp, kwargs["settings_snapshot"]["use_temp"])) or stream_runtime
            )

            bootstrap = _prepare_recording_session_bootstrap(
                rec_type="mic",
                settings_snapshot={"use_temp": False},
                lang_source="English",
                engine="Whisper",
                model_name_tc="base",
                is_tc=True,
                is_tl=False,
                p=object(),
            )
        finally:
            record_module._build_recording_session_config = previous_build_config
            record_module._load_recording_model_runtime = previous_load_runtime
            record_module._build_recording_stream_runtime = previous_build_stream

        self.assertIs(bootstrap.config, config)
        self.assertIs(bootstrap.model_runtime, model_runtime)
        self.assertIs(bootstrap.stream_runtime, stream_runtime)
        self.assertEqual(calls, [("config", False), ("model", False), ("stream", True, False)])

    def test_build_recording_stream_runtime_uses_device_and_vad_bootstrap(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_get_device_details = record_module.get_device_details
        previous_load_vad = record_module._load_recording_vad_runtime
        previous_init_ctx = record_module._initialize_callback_context
        try:
            record_module.get_device_details = lambda rec_type, sj, p: (
                True,
                {
                    "device_detail": {"index": 7},
                    "sample_rate": 48000,
                    "num_of_channels": 2,
                    "chunk_size": 960,
                },
            )
            record_module._load_recording_vad_runtime = lambda rec_type, settings_snapshot=None: ("webrtc", "silero")
            record_module._initialize_callback_context = lambda **kwargs: RealtimeCallbackContext(
                sample_rate=kwargs["sample_rate"],
                frame_duration_ms=10,
                threshold_enable=kwargs["threshold_enable"],
                threshold_db=kwargs["threshold_db"],
                threshold_auto=kwargs["threshold_auto"],
                use_silero=kwargs["use_silero"],
                silero_min_conf=kwargs["silero_min_conf"],
                vad_checked=False,
                num_of_channels=kwargs["num_of_channels"],
                samp_width=kwargs["samp_width"],
                use_temp=kwargs["use_temp"],
                webrtc_vad=kwargs["webrtc_vad"],
                silero_vad=kwargs["silero_vad"],
                silence_started_at=0.0,
            )

            config = _build_recording_session_config(
                rec_type="mic",
                lang_source="English",
                engine="Whisper",
                is_tc=True,
                is_tl=True,
            )
            runtime = _build_recording_stream_runtime(rec_type="mic", config=config, p=type("P", (), {"get_sample_size": lambda self, fmt: 2})())
        finally:
            record_module.get_device_details = previous_get_device_details
            record_module._load_recording_vad_runtime = previous_load_vad
            record_module._initialize_callback_context = previous_init_ctx

        self.assertIsInstance(runtime, RecordingStreamRuntime)
        self.assertEqual(runtime.input_device_index, 7)
        self.assertEqual(runtime.sr_ori, 48000)
        self.assertEqual(runtime.num_of_channels, 2)
        self.assertEqual(runtime.chunk_size, 960)
        self.assertEqual(runtime.samp_width, 2)
        self.assertEqual(runtime.sr_divider, 16000)
        self.assertEqual(runtime.callback_ctx.webrtc_vad, "webrtc")
        self.assertEqual(runtime.callback_ctx.silero_vad, "silero")

    def test_build_recording_stream_runtime_uses_explicit_settings_snapshot(self) -> None:
        from speech_translate.utils.audio import record_streaming as streaming_module

        captured = {}

        def fake_load_vad_runtime(*, rec_type, settings_snapshot=None):
            captured["rec_type"] = rec_type
            captured["settings_snapshot"] = dict(settings_snapshot)
            return "webrtc", "silero"

        runtime = streaming_module.build_recording_stream_runtime(
            rec_type="mic",
            config=type(
                "Config",
                (),
                {
                    "threshold_enable": True,
                    "threshold_db": -20.0,
                    "threshold_auto": True,
                    "use_silero": True,
                    "silero_min_conf": 0.75,
                    "use_temp": False,
                },
            )(),
            p=type("P", (), {"get_sample_size": lambda self, fmt: 2})(),
            get_device_details_fn=lambda rec_type, sj, p: (
                True,
                {
                    "device_detail": {"index": 1},
                    "sample_rate": 44100,
                    "num_of_channels": 1,
                    "chunk_size": 441,
                },
            ),
            load_recording_vad_runtime_fn=fake_load_vad_runtime,
            initialize_callback_context_fn=lambda **kwargs: RealtimeCallbackContext(
                sample_rate=kwargs["sample_rate"],
                frame_duration_ms=10,
                threshold_enable=kwargs["threshold_enable"],
                threshold_db=kwargs["threshold_db"],
                threshold_auto=kwargs["threshold_auto"],
                use_silero=kwargs["use_silero"],
                silero_min_conf=kwargs["silero_min_conf"],
                vad_checked=False,
                num_of_channels=kwargs["num_of_channels"],
                samp_width=kwargs["samp_width"],
                use_temp=kwargs["use_temp"],
                webrtc_vad=kwargs["webrtc_vad"],
                silero_vad=kwargs["silero_vad"],
                silence_started_at=0.0,
            ),
            audio_format=16,
            logger_instance=type("Logger", (), {"warning": lambda self, message: None})(),
            settings_snapshot={
                "threshold_auto_mode_mic": 2,
                "supress_record_warning": True,
            },
        )

        self.assertEqual(captured["rec_type"], "mic")
        self.assertEqual(captured["settings_snapshot"]["threshold_auto_mode_mic"], 2)
        self.assertTrue(captured["settings_snapshot"]["supress_record_warning"])
        self.assertEqual(runtime.input_device_index, 1)
        self.assertEqual(runtime.sr_ori, 44100)

    def test_open_recording_stream_passes_bootstrap_values_to_pyaudio(self) -> None:
        from speech_translate.utils.audio import record as record_module

        class FakeStreamOwner:
            def __init__(self) -> None:
                self.calls = []

            def open(self, **kwargs):
                self.calls.append(kwargs)
                return "stream"

        owner = FakeStreamOwner()
        runtime = RecordingStreamRuntime(
            input_device_index=5,
            sr_ori=44100,
            num_of_channels=1,
            chunk_size=512,
            samp_width=2,
            sr_divider=44100,
            callback_ctx=RealtimeCallbackContext(
                sample_rate=44100,
                frame_duration_ms=10,
                threshold_enable=True,
                threshold_db=-20.0,
                threshold_auto=True,
                use_silero=True,
                silero_min_conf=0.75,
                vad_checked=False,
                num_of_channels=1,
                samp_width=2,
                use_temp=False,
            ),
        )

        previous_record_cb = record_module.record_cb
        state_adapter = FakeStreamingStateAdapter()
        try:
            record_module.record_cb = lambda *args, **kwargs: "cb"
            _open_recording_stream(p=owner, stream_runtime=runtime, state_adapter=state_adapter)
        finally:
            record_module.record_cb = previous_record_cb

        self.assertEqual(owner.calls[0]["channels"], 1)
        self.assertEqual(owner.calls[0]["rate"], 44100)
        self.assertEqual(owner.calls[0]["input_device_index"], 5)
        self.assertEqual(owner.calls[0]["stream_callback"](), "cb")
        self.assertEqual(state_adapter.stream, "stream")

    def test_open_recording_stream_supports_injected_state_adapter(self) -> None:
        from speech_translate.utils.audio import record_streaming as streaming_module

        class FakeStreamOwner:
            def open(self, **kwargs):
                return ("stream", kwargs["rate"])

        runtime = RecordingStreamRuntime(
            input_device_index=5,
            sr_ori=44100,
            num_of_channels=1,
            chunk_size=512,
            samp_width=2,
            sr_divider=44100,
            callback_ctx=RealtimeCallbackContext(
                sample_rate=44100,
                frame_duration_ms=10,
                threshold_enable=True,
                threshold_db=-20.0,
                threshold_auto=True,
                use_silero=True,
                silero_min_conf=0.75,
                vad_checked=False,
                num_of_channels=1,
                samp_width=2,
                use_temp=False,
            ),
        )
        state_adapter = FakeStreamingStateAdapter()

        streaming_module.open_recording_stream(
            p=FakeStreamOwner(),
            stream_runtime=runtime,
            record_cb=lambda *args, **kwargs: "cb",
            state_adapter=state_adapter,
        )

        self.assertEqual(state_adapter.stream, ("stream", 44100))

    def test_build_recording_session_services_wires_runtime_translator_and_reducer(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_dispatcher = record_module.TranslationDispatcher
        try:
            class DispatcherSpy:
                def __init__(self, **kwargs) -> None:
                    self.kwargs = kwargs

            record_module.TranslationDispatcher = DispatcherSpy
            config = _build_recording_session_config(
                rec_type="mic",
                lang_source="English",
                engine="Whisper",
                is_tc=True,
                is_tl=True,
                settings_snapshot={
                    "transcribe_rate": 1000,
                    "max_buffer_mic": 10,
                    "max_sentences_mic": 5,
                    "mic_no_limit": False,
                    "min_input_length_mic": 0.4,
                    "keep_temp": True,
                    "threshold_enable_mic": True,
                    "threshold_db_mic": -20,
                    "threshold_auto_mic": True,
                    "threshold_auto_silero_mic": True,
                    "threshold_silero_mic_min": 0.75,
                    "auto_break_buffer_mic": True,
                    "use_temp": False,
                    "separate_with": repr("\n"),
                },
            )

            class RuntimeStub:
                hallucination_filters = {"english": ["x"]}
                stable_tl = object()
                whisper_args = {"foo": "bar"}
                use_temp = False

            services = _build_recording_session_services(
                config=config,
                model_runtime=RuntimeStub(),
                device="mic",
                lang_source="English",
                lang_target="Chinese",
                engine="Whisper",
                is_tc=True,
                is_tl=True,
                t_start=12.0,
                control=FakeRecordingSessionControl(),
            )
        finally:
            record_module.TranslationDispatcher = previous_dispatcher

        self.assertIsInstance(services, RecordingSessionServices)
        self.assertEqual(services.runtime.device, "mic")
        self.assertTrue(services.runtime.keep_temp)
        self.assertIsInstance(services.status_emitter, RecordingStatusEmitter)
        self.assertIsInstance(services.buffer_reducer, BufferStateReducer)
        self.assertEqual(services.translator.kwargs["lang_target"], "Chinese")
        self.assertEqual(services.translator.kwargs["hallucination_filters"], {"english": ["x"]})

    def test_initialize_recording_session_lifecycle_resets_session_state_and_lock(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_build_services = record_module._build_recording_session_services
        try:
            runtime = RecordingRuntime(
                taskname="Transcribe & Translate",
                device="mic",
                lang_source="English",
                lang_target="Chinese",
                engine="Whisper",
                is_tl=True,
                use_temp=False,
                separator="<br />",
                keep_temp=False,
                t_start=1.0,
                max_buffer_s=10.0,
                max_sentences=5,
                sentence_limitless=False,
                lang_target_display="Chinese",
            )
            services = RecordingSessionServices(
                runtime=runtime,
                status_emitter=object(),
                translator=object(),
                buffer_reducer=object(),
            )
            record_module._build_recording_session_services = lambda **kwargs: services
            control = FakeRecordingSessionControl(status="busy")
            runtime_text_state = FakeRuntimeTextState(
                tc_sentences=["old"],
                tl_sentences=["old-tl"],
                detected_language="en",
                prev_tc_res="prev",
                prev_tl_res="prev-tl",
            )

            class ModelRuntimeStub:
                pass

            lifecycle = _initialize_recording_session_lifecycle(
                config=type("Config", (), {"tl_engine_whisper": True})(),
                model_runtime=ModelRuntimeStub(),
                stream_runtime=RecordingStreamRuntime(
                    input_device_index=0,
                    sr_ori=16000,
                    num_of_channels=1,
                    chunk_size=320,
                    samp_width=2,
                    sr_divider=16000,
                    callback_ctx=RealtimeCallbackContext(
                        sample_rate=16000,
                        frame_duration_ms=20,
                        threshold_enable=True,
                        threshold_db=-20.0,
                        threshold_auto=True,
                        use_silero=True,
                        silero_min_conf=0.75,
                        vad_checked=False,
                        num_of_channels=1,
                        samp_width=2,
                        use_temp=False,
                    ),
                ),
                device="mic",
                lang_source="English",
                lang_target="Chinese",
                engine="Whisper",
                is_tc=True,
                is_tl=True,
                t_start=2.0,
                control=control,
                runtime_text_state=runtime_text_state,
            )
            observed_status = control.current_status()
            observed_auto_lang = runtime_text_state.detected_language()
            observed_tc_sentences = list(runtime_text_state.transcribed_sentences())
            observed_tl_sentences = list(runtime_text_state.translated_sentences())
            observed_prev_tc = runtime_text_state.previous_transcribed_result()
            observed_prev_tl = runtime_text_state.previous_translated_result()
            observed_tc_lock = lifecycle.session_state.transcription_lock
        finally:
            record_module._build_recording_session_services = previous_build_services

        self.assertIsInstance(lifecycle, RecordingSessionLifecycle)
        self.assertEqual(lifecycle.session_state.last_sample, b"")
        self.assertEqual(observed_status, "▶️ Recording (Waiting for speech)")
        self.assertEqual(observed_auto_lang, "~")
        self.assertEqual(observed_tc_sentences, [])
        self.assertEqual(observed_tl_sentences, [])
        self.assertEqual(observed_prev_tc, "")
        self.assertEqual(observed_prev_tl, "")
        self.assertIsNotNone(observed_tc_lock)
        self.assertIs(lifecycle.services, services)
        self.assertEqual(lifecycle.sr_divider, 16000)

    def test_recording_session_finalize_context_defaults_without_lifecycle(self) -> None:
        context = RecordingSessionFinalizeContext.from_lifecycle(None)

        self.assertIsNone(context.session_state)
        self.assertIsNone(context.update_status)
        self.assertTrue(context.keep_temp)

    def test_recording_session_finalize_context_captures_lifecycle_cleanup_contract(self) -> None:
        updated = []
        control = FakeRecordingSessionControl(status="Stopping")
        runtime = RecordingRuntime(
            taskname="Transcribe",
            device="mic",
            lang_source="English",
            lang_target="-",
            engine="Whisper",
            is_tl=False,
            use_temp=True,
            separator="<br />",
            keep_temp=False,
            t_start=0.0,
            max_buffer_s=10.0,
            max_sentences=5,
            sentence_limitless=False,
            lang_target_display="-",
        )
        services = RecordingSessionServices(
            runtime=runtime,
            status_emitter=type("Emitter", (), {"emit": lambda self, **kwargs: updated.append(kwargs)})(),
            translator=object(),
            buffer_reducer=object(),
            control=control,
        )
        lifecycle = RecordingSessionLifecycle(
            session_state=RealtimeSessionState(),
            services=services,
            callback_ctx=RealtimeCallbackContext(
                sample_rate=16000,
                frame_duration_ms=20,
                threshold_enable=True,
                threshold_db=-20.0,
                threshold_auto=True,
                use_silero=True,
                silero_min_conf=0.75,
                vad_checked=False,
                num_of_channels=1,
                samp_width=2,
                use_temp=True,
            ),
            sr_ori=16000,
            num_of_channels=1,
            samp_width=2,
            sr_divider=16000,
        )
        context = RecordingSessionFinalizeContext.from_lifecycle(lifecycle)
        context.update_status()

        self.assertIs(context.session_state, lifecycle.session_state)
        self.assertFalse(context.keep_temp)
        self.assertEqual(updated, [{"status": "Stopping"}])

    def test_start_recording_session_support_threads_starts_workers_and_updates_status(self) -> None:
        from speech_translate.utils.audio import record as record_module

        calls = []
        previous_start_translation = record_module._start_translation_dispatcher_thread
        previous_start_status = record_module._start_recording_status_thread
        try:
            record_module._start_translation_dispatcher_thread = lambda translator, control=None: calls.append(("translation", translator, control))
            record_module._start_recording_status_thread = lambda *args, **kwargs: calls.append(("status", kwargs))

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
            control = FakeRecordingSessionControl(status="Recording")
            emitter = RecordingStatusEmitter(
                runtime,
                bridge_adapter=type(
                    "InjectedBridgeAdapter",
                    (),
                    {
                        "update_task_message": lambda _self, message: bridge.update_task_message(message),
                        "set_recording_state": lambda _self, payload: bridge.set_recording_state(payload),
                    },
                )(),
            )
            services = RecordingSessionServices(
                runtime=runtime,
                status_emitter=emitter,
                translator=object(),
                buffer_reducer=object(),
                control=control,
            )
            state = RealtimeSessionState()

            _start_recording_session_support_threads(
                services=services,
                session_state=state,
                t_start=5.0,
                max_buffer_s=10,
                max_sentences=4,
                sentence_limitless=False,
                control=control,
            )
        finally:
            record_module._start_translation_dispatcher_thread = previous_start_translation
            record_module._start_recording_status_thread = previous_start_status

        self.assertEqual(bridge.messages, ["Recording"])
        self.assertEqual(calls[0][0], "translation")
        self.assertIs(calls[0][2], control)
        self.assertEqual(calls[1][0], "status")
        self.assertEqual(calls[1][1]["max_sentences"], 4)

    def test_record_session_builds_stream_runtime_after_model_runtime_updates_use_temp(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_build_config = record_module._build_recording_session_config
        previous_get_pyaudio_module = record_module.get_pyaudio_module
        previous_load_runtime = record_module._load_recording_model_runtime
        previous_build_stream = record_module._build_recording_stream_runtime
        previous_build_services = record_module._build_recording_session_services
        previous_start_support_threads = record_module._start_recording_session_support_threads
        previous_open_stream = record_module._open_recording_stream
        previous_run_loop = record_module._run_recording_session_loop
        previous_finalize = record_module._finalize_recording_session
        observed = {}
        try:
            class ConfigStub:
                rec_type = "mic"
                transcribe_rate = timedelta(seconds=1)
                max_buffer_s = 10
                max_sentences = 5
                sentence_limitless = False
                tl_engine_whisper = False
                taskname = "Transcribe"
                auto = False
                threshold_enable = True
                threshold_db = -20.0
                threshold_auto = True
                use_silero = True
                silero_min_conf = 0.75
                auto_break_buffer = True
                use_temp = False
                separator = "<br />"

            config = ConfigStub()
            record_module._build_recording_session_config = lambda **kwargs: config
            record_module.get_pyaudio_module = lambda: type("FakePyAudioModule", (), {"PyAudio": lambda self: object()})()

            class ModelRuntimeStub:
                use_temp = True
                cuda_device = "cpu"
                demucs_enabled = False
                hallucination_filters = {}
                stable_tl = None
                whisper_args = {}

            record_module._load_recording_model_runtime = lambda **kwargs: ModelRuntimeStub()

            def fake_build_stream_runtime(
                *,
                rec_type,
                config,
                p,
                settings_snapshot=None,
                shared_runtime_state=None,
                callback_context_store_instance=None,
            ):
                observed["use_temp_seen"] = config.use_temp
                observed["settings_snapshot_use_temp"] = settings_snapshot["use_temp"]
                observed["shared_runtime_state"] = shared_runtime_state
                observed["callback_context_store_instance"] = callback_context_store_instance
                return RecordingStreamRuntime(
                    input_device_index=0,
                    sr_ori=16000,
                    num_of_channels=1,
                    chunk_size=320,
                    samp_width=2,
                    sr_divider=16000,
                    callback_ctx=RealtimeCallbackContext(
                        sample_rate=16000,
                        frame_duration_ms=20,
                        threshold_enable=True,
                        threshold_db=-20.0,
                        threshold_auto=True,
                        use_silero=True,
                        silero_min_conf=0.75,
                        vad_checked=False,
                        num_of_channels=1,
                        samp_width=2,
                        use_temp=True,
                    ),
                )

            record_module._build_recording_stream_runtime = fake_build_stream_runtime
            def fake_build_services(**kwargs):
                observed["session_control"] = kwargs["control"]
                observed["runtime_text_state"] = kwargs["runtime_text_state"]
                return RecordingSessionServices(
                    runtime=RecordingRuntime(
                        taskname="Transcribe",
                        device="mic",
                        lang_source="English",
                        lang_target="-",
                        engine="Whisper",
                        is_tl=False,
                        use_temp=True,
                        separator="<br />",
                        keep_temp=False,
                        t_start=0.0,
                        max_buffer_s=10.0,
                        max_sentences=5,
                        sentence_limitless=False,
                        lang_target_display="-",
                    ),
                    status_emitter=type("Emitter", (), {"emit": lambda self, **kwargs: None})(),
                    translator=object(),
                    buffer_reducer=object(),
                )

            record_module._build_recording_session_services = fake_build_services
            record_module._start_recording_session_support_threads = lambda **kwargs: None
            def fake_open_stream(**kwargs):
                observed["open_stream_kwargs"] = kwargs

            record_module._open_recording_stream = fake_open_stream
            record_module._run_recording_session_loop = lambda **kwargs: None
            record_module._finalize_recording_session = lambda *args, **kwargs: None

            record_session(
                RecordingSessionRequest(
                    lang_source="English",
                    lang_target="Chinese",
                    engine="Whisper",
                    model_name_tc="base",
                    device="mic",
                    is_tc=True,
                    is_tl=False,
                )
            )
        finally:
            record_module._build_recording_session_config = previous_build_config
            record_module.get_pyaudio_module = previous_get_pyaudio_module
            record_module._load_recording_model_runtime = previous_load_runtime
            record_module._build_recording_stream_runtime = previous_build_stream
            record_module._build_recording_session_services = previous_build_services
            record_module._start_recording_session_support_threads = previous_start_support_threads
            record_module._open_recording_stream = previous_open_stream
            record_module._run_recording_session_loop = previous_run_loop
            record_module._finalize_recording_session = previous_finalize

        self.assertTrue(observed["use_temp_seen"])
        self.assertFalse(observed["settings_snapshot_use_temp"])
        self.assertIsInstance(observed["shared_runtime_state"], RealtimeSharedState)
        self.assertIsInstance(observed["session_control"], record_module.RecordingSessionControl)
        self.assertIsNot(
            observed["runtime_text_state"]._shared,
            build_recording_text_state()._shared,
        )
        self.assertIs(observed["runtime_text_state"]._shared, observed["shared_runtime_state"])
        self.assertIsNotNone(observed["callback_context_store_instance"])
        self.assertTrue(callable(observed["open_stream_kwargs"]["record_cb_override"]))
        self.assertIsNot(observed["open_stream_kwargs"]["record_cb_override"], record_module.record_cb)
        self.assertIs(
            observed["open_stream_kwargs"]["state_adapter"].runtime_state,
            observed["session_control"].runtime_state,
        )

    def test_record_session_finalizes_when_failure_happens_after_pyaudio_bootstrap(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_build_config = record_module._build_recording_session_config
        previous_get_pyaudio_module = record_module.get_pyaudio_module
        previous_load_runtime = record_module._load_recording_model_runtime
        previous_build_stream = record_module._build_recording_stream_runtime
        previous_finalize = record_module._finalize_recording_session
        previous_empty_torch_cuda_cache = record_module.empty_torch_cuda_cache
        finalized = []
        try:
            class ConfigStub:
                use_temp = False

            py_audio = object()
            record_module._build_recording_session_config = lambda **kwargs: ConfigStub()
            record_module.get_pyaudio_module = lambda: type("FakePyAudioModule", (), {"PyAudio": lambda self: py_audio})()
            record_module._load_recording_model_runtime = lambda **kwargs: type(
                "ModelRuntime",
                (),
                {"use_temp": False, "cuda_device": "cpu", "demucs_enabled": False},
            )()
            record_module._build_recording_stream_runtime = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
            record_module._finalize_recording_session = lambda *args, **kwargs: finalized.append((args, kwargs))
            record_module.empty_torch_cuda_cache = lambda: None

            record_session(
                RecordingSessionRequest(
                    lang_source="English",
                    lang_target="Chinese",
                    engine="Whisper",
                    model_name_tc="base",
                    device="mic",
                    is_tc=True,
                    is_tl=False,
                )
            )
        finally:
            record_module._build_recording_session_config = previous_build_config
            record_module.get_pyaudio_module = previous_get_pyaudio_module
            record_module._load_recording_model_runtime = previous_load_runtime
            record_module._build_recording_stream_runtime = previous_build_stream
            record_module._finalize_recording_session = previous_finalize
            record_module.empty_torch_cuda_cache = previous_empty_torch_cuda_cache

        self.assertEqual(finalized[0][0][0], py_audio)
        self.assertIsInstance(finalized[0][0][1], RecordingSessionFinalizeContext)
        self.assertIsNone(finalized[0][0][1].session_state)
        self.assertIsNone(finalized[0][0][1].update_status)
        self.assertTrue(finalized[0][0][1].keep_temp)

    def test_record_session_uses_injected_session_dependencies(self) -> None:
        from queue import Queue
        from speech_translate.utils.audio import record as record_module

        previous_build_config = record_module._build_recording_session_config
        previous_get_pyaudio_module = record_module.get_pyaudio_module
        previous_load_runtime = record_module._load_recording_model_runtime
        previous_build_stream = record_module._build_recording_stream_runtime
        previous_build_services = record_module._build_recording_session_services
        previous_start_support_threads = record_module._start_recording_session_support_threads
        previous_open_stream = record_module._open_recording_stream
        previous_run_loop = record_module._run_recording_session_loop
        previous_finalize = record_module._finalize_recording_session
        observed = {}
        injected_runtime_state = build_recording_runtime_state_adapter(
            state=BridgeRecordingRuntime(recording=True, data_queue=Queue()),
        )
        injected_control = build_recording_session_control(runtime_state=injected_runtime_state)
        injected_text_state = build_recording_text_state(
            shared_runtime_state=RealtimeSharedState(),
            text_store=build_recording_text_store_adapter(state=BridgeLiveTextRuntime()),
        )
        injected_store = FakeCallbackContextStore()
        injected_settings_snapshot = {
            "transcribe_rate": 1000,
            "max_buffer_mic": 10,
            "max_sentences_mic": 5,
            "mic_no_limit": False,
            "min_input_length_mic": 0.4,
            "keep_temp": False,
            "threshold_enable_mic": True,
            "threshold_db_mic": -20.0,
            "threshold_auto_mic": True,
            "threshold_auto_silero_mic": True,
            "threshold_silero_mic_min": 0.75,
            "auto_break_buffer_mic": True,
            "use_temp": False,
            "separate_with": repr("\n"),
        }
        try:
            class ConfigStub:
                use_temp = False
                taskname = "Transcribe"
                max_buffer_s = 10
                max_sentences = 5
                sentence_limitless = False

            record_module._build_recording_session_config = lambda **kwargs: ConfigStub()
            record_module.get_pyaudio_module = lambda: type("FakePyAudioModule", (), {"PyAudio": lambda self: object()})()
            record_module._load_recording_model_runtime = lambda **kwargs: type(
                "ModelRuntime",
                (),
                {"use_temp": False, "cuda_device": "cpu", "demucs_enabled": False, "hallucination_filters": {}, "stable_tl": None, "whisper_args": {}},
            )()

            def fake_build_stream_runtime(*, settings_snapshot=None, shared_runtime_state=None, callback_context_store_instance=None, **kwargs):
                observed["settings_snapshot"] = settings_snapshot
                observed["shared_runtime_state"] = shared_runtime_state
                observed["callback_context_store_instance"] = callback_context_store_instance
                return RecordingStreamRuntime(
                    input_device_index=0,
                    sr_ori=16000,
                    num_of_channels=1,
                    chunk_size=320,
                    samp_width=2,
                    sr_divider=16000,
                    callback_ctx=RealtimeCallbackContext(
                        sample_rate=16000,
                        frame_duration_ms=20,
                        threshold_enable=True,
                        threshold_db=-20.0,
                        threshold_auto=True,
                        use_silero=True,
                        silero_min_conf=0.75,
                        vad_checked=False,
                        num_of_channels=1,
                        samp_width=2,
                        use_temp=False,
                    ),
                )

            record_module._build_recording_stream_runtime = fake_build_stream_runtime

            def fake_build_services(**kwargs):
                observed["control"] = kwargs["control"]
                observed["runtime_text_state"] = kwargs["runtime_text_state"]
                return RecordingSessionServices(
                    runtime=RecordingRuntime(
                        taskname="Transcribe",
                        device="mic",
                        lang_source="English",
                        lang_target="-",
                        engine="Whisper",
                        is_tl=False,
                        use_temp=False,
                        separator="<br />",
                        keep_temp=False,
                        t_start=0.0,
                        max_buffer_s=10.0,
                        max_sentences=5,
                        sentence_limitless=False,
                        lang_target_display="-",
                    ),
                    status_emitter=type("Emitter", (), {"emit": lambda self, **kwargs: None})(),
                    translator=object(),
                    buffer_reducer=object(),
                )

            record_module._build_recording_session_services = fake_build_services
            record_module._start_recording_session_support_threads = lambda **kwargs: None
            record_module._open_recording_stream = lambda **kwargs: observed.setdefault("open_stream_kwargs", kwargs)
            record_module._run_recording_session_loop = lambda **kwargs: None
            record_module._finalize_recording_session = lambda *args, **kwargs: None

            record_session(
                RecordingSessionRequest(
                    lang_source="English",
                    lang_target="Chinese",
                    engine="Whisper",
                    model_name_tc="base",
                    device="mic",
                    is_tc=True,
                    is_tl=False,
                ),
                dependencies=RecordingSessionDependencies(
                    settings_snapshot=injected_settings_snapshot,
                    session_control=injected_control,
                    runtime_text_state=injected_text_state,
                    callback_context_store=injected_store,
                ),
            )
        finally:
            record_module._build_recording_session_config = previous_build_config
            record_module.get_pyaudio_module = previous_get_pyaudio_module
            record_module._load_recording_model_runtime = previous_load_runtime
            record_module._build_recording_stream_runtime = previous_build_stream
            record_module._build_recording_session_services = previous_build_services
            record_module._start_recording_session_support_threads = previous_start_support_threads
            record_module._open_recording_stream = previous_open_stream
            record_module._run_recording_session_loop = previous_run_loop
            record_module._finalize_recording_session = previous_finalize

        self.assertEqual(observed["settings_snapshot"], injected_settings_snapshot)
        self.assertIsNot(observed["settings_snapshot"], injected_settings_snapshot)
        self.assertIs(observed["shared_runtime_state"], injected_text_state._shared)
        self.assertIs(observed["callback_context_store_instance"], injected_store)
        self.assertIs(observed["control"], injected_control)
        self.assertIs(observed["runtime_text_state"], injected_text_state)
        self.assertIs(observed["open_stream_kwargs"]["state_adapter"].runtime_state, injected_runtime_state)

    def test_resolve_live_input_source_language_prefers_detected_supported_language(self) -> None:
        runtime_text_state = FakeRuntimeTextState(detected_language="en")
        resolved = _resolve_live_input_source_language("Auto Detect", "Google Translate", runtime_text_state)

        self.assertEqual(resolved, "english")

    def test_normalize_translation_result_units_aligns_with_source_units(self) -> None:
        aligned = _normalize_translation_result_units([" one ", "", "three"], ["a", "b", "c"])
        self.assertEqual(aligned, ["one", "three"])

    def test_merge_translation_units_preserves_sentence_spacing_rules(self) -> None:
        merged = _merge_translation_units(["hello", "world", "!"])
        self.assertEqual(merged, ["hello world", "!"])

    def test_build_recording_sentence_count_text_includes_limit_when_enabled(self) -> None:
        runtime_text_state = FakeRuntimeTextState(tc_sentences=["a", "b"], tl_sentences=[])
        count_text = _build_recording_sentence_count_text(
            sentence_limitless=False,
            max_sentences=5,
            runtime_text_state=runtime_text_state,
        )

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
        state = RealtimeSessionState(last_sample=b"")
        control = FakeRecordingSessionControl(queue_items=[b"ab", b"cd"])
        _drain_pending_audio(state, control=control)

        self.assertEqual(state.last_sample, b"abcd")

    def test_drain_pending_audio_supports_injected_session_control(self) -> None:
        from speech_translate.utils.audio import record as record_module

        state = RealtimeSessionState(last_sample=b"")
        control = FakeRecordingSessionControl(queue_items=[b"ab", b"cd"])

        record_module._drain_pending_audio(state, control=control)

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
            control=FakeRecordingSessionControl(),
        )
        self.assertFalse(ready)
        self.assertEqual(state.last_sample, b"ab")

    def test_cleanup_processed_audio_target_removes_non_whisper_temp_file(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_remove = record_module.os.remove
        removed = []
        state = RealtimeSessionState(temp_audio_paths=["temp.wav"])
        try:
            record_module.os.remove = lambda path: removed.append(path)
            _cleanup_processed_audio_target(
                "temp.wav",
                use_temp=True,
                keep_temp=False,
                is_tl=False,
                tl_engine_whisper=False,
                session_state=state,
            )
        finally:
            record_module.os.remove = previous_remove

        self.assertEqual(removed, ["temp.wav"])
        self.assertEqual(state.temp_audio_paths, [])

    def test_execute_recording_iteration_dispatches_whisper_translation_only(self) -> None:
        translator = FakeTranslator()
        state = RealtimeSessionState(duration_seconds=1.2)
        config = _build_recording_session_config(
            rec_type="mic",
            lang_source="English",
            engine="Whisper",
            is_tc=False,
            is_tl=True,
        )
        config.tl_engine_whisper = True

        class RuntimeStub:
            stable_tc = None
            whisper_args = {}
            hallucination_filters = {}
            configured_whisper_language = None

        executed = _execute_recording_iteration(
            audio_target="temp.wav",
            session_state=state,
            is_tc=False,
            is_tl=True,
            config=config,
            model_runtime=RuntimeStub(),
            translator=translator,
            control=FakeRecordingSessionControl(),
        )

        self.assertTrue(executed)
        self.assertEqual(translator.calls[-1], ("temp.wav", ""))

    def test_run_recording_session_loop_executes_iteration_and_resets_transcribing_status(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_consume = record_module._consume_record_loop_input
        previous_advance = record_module._advance_recording_buffer
        previous_build_target = record_module._build_record_audio_target
        previous_execute = record_module._execute_recording_iteration
        previous_cleanup = record_module._cleanup_processed_audio_target
        previous_break = record_module._break_buffer_and_update_state
        calls = []
        control = FakeRecordingSessionControl(recording=True, status="busy")
        try:
            record_module._consume_record_loop_input = lambda *args, **kwargs: b"abc"
            record_module._advance_recording_buffer = lambda session_state, data, **kwargs: True
            record_module._build_record_audio_target = lambda *args, **kwargs: "temp.wav"

            def fake_execute(**kwargs):
                control.set_current_status("▶️ Recording ⟳ Transcribing Audio")
                kwargs["session_state"].duration_seconds = 1.0
                control.set_recording(False)
                return True

            record_module._execute_recording_iteration = fake_execute
            record_module._cleanup_processed_audio_target = lambda *args, **kwargs: calls.append(("cleanup", args[0]))
            record_module._break_buffer_and_update_state = lambda **kwargs: calls.append(("break", kwargs["reason"]))

            lifecycle = RecordingSessionLifecycle(
                session_state=RealtimeSessionState(),
                services=RecordingSessionServices(
                    runtime=RecordingRuntime(
                        taskname="Transcribe",
                        device="mic",
                        lang_source="English",
                        lang_target="-",
                        engine="Whisper",
                        is_tl=False,
                        use_temp=True,
                        separator="<br />",
                        keep_temp=False,
                        t_start=0.0,
                        max_buffer_s=10.0,
                        max_sentences=5,
                        sentence_limitless=False,
                        lang_target_display="-",
                    ),
                    status_emitter=object(),
                    translator=FakeTranslator(),
                    buffer_reducer=FakeBufferReducer(),
                ),
                callback_ctx=RealtimeCallbackContext(
                    sample_rate=16000,
                    frame_duration_ms=20,
                    threshold_enable=True,
                    threshold_db=-20.0,
                    threshold_auto=True,
                    use_silero=True,
                    silero_min_conf=0.75,
                    vad_checked=False,
                    num_of_channels=1,
                    samp_width=2,
                    use_temp=True,
                ),
                sr_ori=16000,
                num_of_channels=1,
                samp_width=2,
                sr_divider=16000,
            )
            config = _build_recording_session_config(
                rec_type="mic",
                lang_source="English",
                engine="Whisper",
                is_tc=True,
                is_tl=False,
            )

            class RuntimeStub:
                demucs_enabled = False
                cuda_device = "cpu"

            _run_recording_session_loop(
                lifecycle=lifecycle,
                config=config,
                model_runtime=RuntimeStub(),
                is_tc=True,
                is_tl=False,
                rec_type="mic",
                control=control,
            )
            observed_status = control.current_status()
        finally:
            record_module._consume_record_loop_input = previous_consume
            record_module._advance_recording_buffer = previous_advance
            record_module._build_record_audio_target = previous_build_target
            record_module._execute_recording_iteration = previous_execute
            record_module._cleanup_processed_audio_target = previous_cleanup
            record_module._break_buffer_and_update_state = previous_break

        self.assertEqual(calls, [("cleanup", "temp.wav")])
        self.assertEqual(observed_status, "▶️ Recording")

    def test_finalize_recording_session_supports_injected_control(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_reset = record_module._reset_callback_context
        updates = []
        stream_events = []
        control = FakeRecordingSessionControl(
            recording=False,
            status="busy",
            queue_items=[b"leftover"],
            stream=type(
                "FakeStream",
                (),
                {
                    "stop_stream": lambda _self: stream_events.append("stop"),
                    "close": lambda _self: stream_events.append("close"),
                },
            )(),
        )
        terminated = []
        try:
            record_module._reset_callback_context = lambda: updates.append("reset")
            finalize_context = RecordingSessionFinalizeContext(
                session_state=RealtimeSessionState(temp_audio_paths=[]),
                update_status=lambda: updates.append(control.current_status()),
                keep_temp=True,
            )
            py_audio = type("FakePyAudio", (), {"terminate": lambda _self: terminated.append("terminated")})()

            record_module._finalize_recording_session(py_audio, finalize_context, control=control)
        finally:
            record_module._reset_callback_context = previous_reset

        self.assertEqual(stream_events, ["stop", "close"])
        self.assertEqual(terminated, ["terminated"])
        self.assertEqual(updates[:2], ["⚠️ Stopping stream", "⚠️ Terminating pyaudio"])
        self.assertEqual(updates[-2:], ["reset", "⏹️ Stopped"])
        self.assertTrue(control.runtime_threads_cleared)
        self.assertIsNone(control.stream())
        self.assertTrue(control.data_queue_empty())

    def test_run_recording_status_loop_supports_injected_control_and_text_state(self) -> None:
        from speech_translate.utils.audio import record as record_module

        emitted = []
        control = FakeRecordingSessionControl(recording=True, status="Injected")
        runtime_text_state = FakeRuntimeTextState(tc_sentences=["a"])
        session_state = RealtimeSessionState(duration_seconds=1.5)
        emitter = type(
            "Emitter",
            (),
            {
                "emit": lambda _self, **payload: (emitted.append(payload), control.set_recording(False)),
            },
        )()

        record_module._run_recording_status_loop(
            session_state,
            emitter,
            t_start=0.0,
            max_buffer_s=10,
            max_sentences=5,
            sentence_limitless=False,
            control=control,
            runtime_text_state=runtime_text_state,
        )

        self.assertEqual(emitted[0]["status"], "Injected")
        self.assertEqual(emitted[0]["sentences"], "1/5")

    def test_calculate_buffer_duration_handles_invalid_denominator(self) -> None:
        self.assertEqual(
            _calculate_buffer_duration(b"abcd", samp_width=0, num_of_channels=1, sr_divider=16000),
            0.0,
        )

    def test_execute_realtime_transcription_uses_lock_when_present(self) -> None:
        calls = []

        def stable_tc(audio_target, **kwargs):
            calls.append((audio_target, kwargs["task"]))
            return FakeResult("ok")

        result = _execute_realtime_transcription(
            "audio",
            stable_tc,
            {"beam_size": 5},
            transcription_lock=FakeLock(),
        )

        self.assertEqual(result.text, "ok")
        self.assertEqual(calls, [("audio", "transcribe")])

    def test_filter_realtime_transcription_result_uses_configured_language(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_remove = record_module.remove_segments_by_str
        captured = {}

        def fake_remove(result, filters, *args):
            captured["filters"] = filters
            return result

        try:
            record_module.remove_segments_by_str = fake_remove
            filtered = _filter_realtime_transcription_result(
                FakeResult("hello", language="en"),
                hallucination_filters={"english": ["x"]},
                auto=False,
                configured_language="english",
                settings=FakeSettingsStore(
                    {
                        "filter_rec": True,
                        "filter_rec_case_sensitive": False,
                        "filter_rec_strip": True,
                        "filter_rec_ignore_punctuations": False,
                        "filter_rec_exact_match": False,
                        "filter_rec_similarity": 1.0,
                        "debug_realtime_record": False,
                    }
                ),
            )
        finally:
            record_module.remove_segments_by_str = previous_remove

        self.assertIsNotNone(filtered)
        self.assertEqual(captured["filters"], ["x"])

    def test_filter_realtime_transcription_result_uses_detected_language_when_auto(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_remove = record_module.remove_segments_by_str
        captured = {}

        def fake_remove(result, filters, *args):
            captured["filters"] = filters
            return result

        try:
            record_module.remove_segments_by_str = fake_remove
            filtered = _filter_realtime_transcription_result(
                FakeResult("hello", language="en"),
                hallucination_filters={"english": ["y"]},
                auto=True,
                configured_language=None,
                settings=FakeSettingsStore(
                    {
                        "filter_rec": True,
                        "filter_rec_case_sensitive": False,
                        "filter_rec_strip": True,
                        "filter_rec_ignore_punctuations": False,
                        "filter_rec_exact_match": False,
                        "filter_rec_similarity": 1.0,
                        "debug_realtime_record": False,
                    }
                ),
            )
        finally:
            record_module.remove_segments_by_str = previous_remove

        self.assertIsNotNone(filtered)
        self.assertEqual(captured["filters"], ["y"])

    def test_commit_realtime_transcription_updates_state_and_dispatches(self) -> None:
        from speech_translate.utils.audio import record_processing as processing_module

        translator = FakeTranslator()
        runtime_text_state = FakeRuntimeTextState(tc_sentences=[])
        statuses = []
        processing_module.commit_realtime_transcription(
            FakeResult("hello", language="en"),
            audio_target="audio",
            is_tl=True,
            separator="<br />",
            translator=translator,
            runtime_text_state=runtime_text_state,
            set_current_status=statuses.append,
        )

        self.assertEqual(runtime_text_state.detected_language(), "en")
        self.assertEqual(runtime_text_state.tc_updates[-1][1], "<br />")
        self.assertEqual(statuses[-1], "▶️ Recording ⟳ Translating text")
        self.assertEqual(translator.calls[-1], ("audio", "hello"))

    def test_commit_realtime_transcription_supports_injected_text_state_and_status_setter(self) -> None:
        from speech_translate.utils.audio import record_processing as processing_module

        translator = FakeTranslator()
        runtime_text_state = FakeRuntimeTextState(tc_sentences=["old"])
        statuses = []

        processing_module.commit_realtime_transcription(
            FakeResult("hello", language="en"),
            audio_target="audio",
            is_tl=True,
            separator="<br />",
            translator=translator,
            runtime_text_state=runtime_text_state,
            set_current_status=statuses.append,
        )

        self.assertEqual(runtime_text_state.detected_language(), "en")
        self.assertEqual(_result_text(runtime_text_state.previous_transcribed_result()), "hello")
        self.assertEqual(runtime_text_state.tc_updates[-1][1], "<br />")
        self.assertEqual(statuses[-1], "▶️ Recording ⟳ Translating text")
        self.assertEqual(translator.calls[-1], ("audio", "old\nhello"))

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
        meter_state = RealtimeSharedState()
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
            shared_runtime_state=meter_state,
        )
        try:
            record_module.get_db = lambda _: -10.0
            is_speech, payload = _detect_realtime_speech(ctx, b"orig", b"resampled")
        finally:
            record_module.get_db = previous_get_db

        self.assertTrue(is_speech)
        self.assertEqual(payload, b"resampled")
        self.assertEqual(meter_state.last_db, -10.0)

    def test_update_realtime_queue_state_tracks_silence_edges(self) -> None:
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
        state_adapter = FakeStreamingStateAdapter()
        _update_realtime_queue_state(ctx, is_speech=True, data_to_queue=b"abc", state_adapter=state_adapter)
        queued = state_adapter.queued[-1]
        _update_realtime_queue_state(ctx, is_speech=False, data_to_queue=b"", state_adapter=state_adapter)

        self.assertEqual(queued, b"abc")
        self.assertTrue(ctx.is_silence)

    def test_update_realtime_queue_state_supports_injected_state_adapter(self) -> None:
        from speech_translate.utils.audio import record_streaming as streaming_module

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
        state_adapter = FakeStreamingStateAdapter()

        streaming_module.update_realtime_queue_state(
            ctx,
            is_speech=True,
            data_to_queue=b"abc",
            state_adapter=state_adapter,
        )
        streaming_module.update_realtime_queue_state(
            ctx,
            is_speech=False,
            data_to_queue=b"",
            state_adapter=state_adapter,
        )

        self.assertEqual(state_adapter.queued, [b"abc"])
        self.assertEqual(state_adapter.statuses[-1], "▶️ Recording (Waiting for speech)")

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
        from speech_translate.utils.audio.record_runtime import RecordingBridgeAdapter

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
        emitter = RecordingStatusEmitter(runtime, bridge_adapter=RecordingBridgeAdapter(bridge=bridge))
        emitter.emit(status="Recording", timer="00:00:01", buffer_text="1.0/10.0 sec", sentences="2/5")

        self.assertEqual(bridge.messages, ["Recording"])
        self.assertEqual(bridge.states[-1]["status"], "Recording")
        self.assertEqual(bridge.states[-1]["timer"], "00:00:01")

    def test_recording_status_emitter_supports_injected_bridge_adapter(self) -> None:
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
        emitter = RecordingStatusEmitter(
            runtime,
            bridge_adapter=type(
                "InjectedBridgeAdapter",
                (),
                {
                    "update_task_message": lambda _self, message: bridge.update_task_message(message),
                    "set_recording_state": lambda _self, payload: bridge.set_recording_state(payload),
                },
            )(),
        )

        emitter.emit(status="Recording", timer="00:00:02")

        self.assertEqual(bridge.messages, ["Recording"])
        self.assertEqual(bridge.states[-1]["timer"], "00:00:02")

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

        runtime_text_state = FakeRuntimeTextState(tc_sentences=["old"], tl_sentences=[])
        runtime_text_state.set_previous_transcribed_result(FakeResult("new"))
        runtime_text_state.set_previous_translated_result(FakeResult("translated"))

        reducer = BufferStateReducer(
            is_tc=True,
            is_tl=True,
            tl_engine_whisper=True,
            sentence_limitless=False,
            max_sentences=2,
            separator="<br />",
            translator=translator,
            runtime_text_state=runtime_text_state,
        )
        reducer.reduce_sentences()

        self.assertEqual(runtime_text_state.tc_updates[-1][1], "<br />")
        self.assertEqual(runtime_text_state.tl_updates[-1][1], "<br />")
        self.assertEqual(translator.calls[-1], (None, "old\nnew"))

    def test_buffer_state_reducer_can_use_injected_text_state(self) -> None:
        translator = FakeTranslator()
        runtime_text_state = FakeRuntimeTextState(
            tc_sentences=["old"],
            tl_sentences=[],
            prev_tc_res=FakeResult("new"),
            prev_tl_res=FakeResult("translated"),
        )
        reducer = BufferStateReducer(
            is_tc=True,
            is_tl=True,
            tl_engine_whisper=True,
            sentence_limitless=False,
            max_sentences=2,
            separator="<br />",
            translator=translator,
            runtime_text_state=runtime_text_state,
        )

        reducer.reduce_sentences()

        self.assertEqual(runtime_text_state.tc_updates[-1][1], "<br />")
        self.assertEqual(runtime_text_state.tl_updates[-1][1], "<br />")
        self.assertEqual(len(runtime_text_state.transcribed_sentences()), 2)
        self.assertEqual(_result_text(runtime_text_state.transcribed_sentences()[0]), "old")
        self.assertEqual(_result_text(runtime_text_state.transcribed_sentences()[-1]), "new")
        self.assertEqual(translator.calls[-1], (None, "old\nnew"))

    def test_tl_api_uses_injected_text_state_for_detected_language_and_output(self) -> None:
        from speech_translate.utils.audio import record_runtime as record_runtime_module

        previous_translate = record_runtime_module.translate
        runtime_text_state = FakeRuntimeTextState(detected_language="en")
        try:
            record_runtime_module.translate = lambda *args, **kwargs: (True, [" ni hao "])
            record_runtime_module.tl_api(
                "hello",
                "Auto Detect",
                "Chinese",
                "Google Translate",
                "<br />",
                runtime_text_state=runtime_text_state,
            )
        finally:
            record_runtime_module.translate = previous_translate

        self.assertEqual(runtime_text_state.translated_sentences(), ["ni hao"])
        self.assertEqual(runtime_text_state.previous_translated_result(), "")
        self.assertEqual(runtime_text_state.tl_updates[-1][1], "<br />")

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
        from speech_translate.utils.audio import record_processing as processing_module

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

        runtime_text_state = FakeRuntimeTextState(tc_sentences=[], prev_tc_res=result)
        session_state.prev_tc_buffer_seconds = 8.0

        applied = processing_module.apply_smart_split(
            session_state=session_state,
            previous_result=result,
            sr_divider=1,
            samp_width=1,
            num_of_channels=1,
            sentence_limitless=False,
            max_sentences=5,
            separator="<br />",
            translator=translator,
            utc_now=lambda: timedelta(seconds=0),
            runtime_text_state=runtime_text_state,
        )

        self.assertTrue(applied)
        self.assertEqual(session_state.last_sample, b"89ABCDEFGHIJ")
        self.assertEqual(session_state.duration_seconds, 12.0)
        self.assertIsNotNone(session_state.next_transcribe_time)
        self.assertEqual(runtime_text_state.tc_updates[-1][1], "<br />")
        self.assertEqual(translator.calls[-1][1], "alpha\nbeta")

    def test_apply_smart_split_supports_injected_text_state(self) -> None:
        from speech_translate.utils.audio import record_processing as processing_module

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
        previous_save = processing_module.save_to_temp
        session_state = RealtimeSessionState(last_sample=b"0123456789ABCDEFGHIJ", duration_seconds=20.0)
        translator = FakeTranslator()
        runtime_text_state = FakeRuntimeTextState(tc_sentences=[], prev_tc_res=result)
        try:
            processing_module.save_to_temp = lambda *_args, **_kwargs: "temp.wav"
            applied = processing_module.apply_smart_split(
                session_state=session_state,
                previous_result=result,
                sr_divider=1,
                samp_width=1,
                num_of_channels=1,
                sentence_limitless=False,
                max_sentences=5,
                separator="<br />",
                translator=translator,
                utc_now=lambda: timedelta(seconds=0),
                runtime_text_state=runtime_text_state,
            )
        finally:
            processing_module.save_to_temp = previous_save

        self.assertTrue(applied)
        self.assertEqual(_result_text(runtime_text_state.previous_transcribed_result()), "beta")
        self.assertEqual(_result_text(runtime_text_state.transcribed_sentences()[0]), "alpha")
        self.assertEqual(runtime_text_state.tc_updates[-1][1], "<br />")
        self.assertEqual(translator.calls[-1], ("temp.wav", "alpha\nbeta"))

    def test_break_buffer_falls_back_to_reducer_when_split_not_preserved(self) -> None:
        session_state = RealtimeSessionState(last_sample=b"1234", duration_seconds=2.0)
        translator = FakeTranslator()
        reducer = FakeBufferReducer()
        runtime_text_state = FakeRuntimeTextState(prev_tc_res="")
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
            runtime_text_state=runtime_text_state,
        )

        self.assertEqual(reducer.calls, 1)
        self.assertEqual(session_state.last_sample, b"")
        self.assertEqual(session_state.duration_seconds, 0.0)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
