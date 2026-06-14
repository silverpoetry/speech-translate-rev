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
    RecordingSessionLifecycle,
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
    record_session,
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
            record_module._load_recording_vad_runtime = lambda rec_type: ("webrtc", "silero")
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

        previous_stream = record_module.bc.stream
        previous_record_cb = record_module.record_cb
        try:
            record_module.record_cb = lambda *args, **kwargs: "cb"
            _open_recording_stream(p=owner, stream_runtime=runtime)
        finally:
            record_module.bc.stream = previous_stream
            record_module.record_cb = previous_record_cb

        self.assertEqual(owner.calls[0]["channels"], 1)
        self.assertEqual(owner.calls[0]["rate"], 44100)
        self.assertEqual(owner.calls[0]["input_device_index"], 5)
        self.assertEqual(owner.calls[0]["stream_callback"](), "cb")

    def test_build_recording_session_services_wires_runtime_translator_and_reducer(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_cache = dict(record_module.sj.cache)
        previous_dispatcher = record_module.TranslationDispatcher
        try:
            record_module.sj.cache["keep_temp"] = True

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
            )
        finally:
            record_module.sj.cache.clear()
            record_module.sj.cache.update(previous_cache)
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
        previous_status = record_module.bc.current_rec_status
        previous_auto_lang = record_module.bc.auto_detected_lang
        previous_tc_sentences = list(record_module.bc.tc_sentences)
        previous_tl_sentences = list(record_module.bc.tl_sentences)
        previous_prev_tc = record_module.shared_state.prev_tc_res
        previous_prev_tl = record_module.shared_state.prev_tl_res
        previous_tc_lock = record_module.bc.tc_lock
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
            record_module.bc.current_rec_status = "busy"
            record_module.bc.auto_detected_lang = "en"
            record_module.bc.tc_sentences = ["old"]
            record_module.bc.tl_sentences = ["old-tl"]
            record_module.shared_state.prev_tc_res = "prev"
            record_module.shared_state.prev_tl_res = "prev-tl"
            record_module.bc.tc_lock = None

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
            )
            observed_auto_lang = record_module.bc.auto_detected_lang
            observed_tc_sentences = list(record_module.bc.tc_sentences)
            observed_tl_sentences = list(record_module.bc.tl_sentences)
            observed_prev_tc = record_module.shared_state.prev_tc_res
            observed_prev_tl = record_module.shared_state.prev_tl_res
            observed_tc_lock = record_module.bc.tc_lock
        finally:
            record_module._build_recording_session_services = previous_build_services
            record_module.bc.current_rec_status = previous_status
            record_module.bc.auto_detected_lang = previous_auto_lang
            record_module.bc.tc_sentences = previous_tc_sentences
            record_module.bc.tl_sentences = previous_tl_sentences
            record_module.shared_state.prev_tc_res = previous_prev_tc
            record_module.shared_state.prev_tl_res = previous_prev_tl
            record_module.bc.tc_lock = previous_tc_lock

        self.assertIsInstance(lifecycle, RecordingSessionLifecycle)
        self.assertEqual(lifecycle.session_state.last_sample, b"")
        self.assertEqual(observed_auto_lang, "~")
        self.assertEqual(observed_tc_sentences, [])
        self.assertEqual(observed_tl_sentences, [])
        self.assertEqual(observed_prev_tc, "")
        self.assertEqual(observed_prev_tl, "")
        self.assertIsNotNone(observed_tc_lock)
        self.assertIs(lifecycle.services, services)
        self.assertEqual(lifecycle.sr_divider, 16000)

    def test_start_recording_session_support_threads_starts_workers_and_updates_status(self) -> None:
        from speech_translate.utils.audio import record as record_module

        calls = []
        previous_start_translation = record_module._start_translation_dispatcher_thread
        previous_start_status = record_module._start_recording_status_thread
        previous_status = record_module.bc.current_rec_status
        try:
            record_module._start_translation_dispatcher_thread = lambda translator: calls.append(("translation", translator))
            record_module._start_recording_status_thread = lambda *args, **kwargs: calls.append(("status", kwargs))
            record_module.bc.current_rec_status = "Recording"

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
            emitter = RecordingStatusEmitter(runtime)
            previous_bridge = record_module.bc.web_bridge
            record_module.bc.web_bridge = bridge
            services = RecordingSessionServices(
                runtime=runtime,
                status_emitter=emitter,
                translator=object(),
                buffer_reducer=object(),
            )
            state = RealtimeSessionState()

            _start_recording_session_support_threads(
                services=services,
                session_state=state,
                t_start=5.0,
                max_buffer_s=10,
                max_sentences=4,
                sentence_limitless=False,
            )
        finally:
            record_module._start_translation_dispatcher_thread = previous_start_translation
            record_module._start_recording_status_thread = previous_start_status
            record_module.bc.current_rec_status = previous_status
            record_module.bc.web_bridge = previous_bridge

        self.assertEqual(bridge.messages, ["Recording"])
        self.assertEqual(calls[0][0], "translation")
        self.assertEqual(calls[1][0], "status")
        self.assertEqual(calls[1][1]["max_sentences"], 4)

    def test_record_session_builds_stream_runtime_after_model_runtime_updates_use_temp(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_build_config = record_module._build_recording_session_config
        previous_pyaudio = record_module.pyaudio.PyAudio
        previous_load_runtime = record_module._load_recording_model_runtime
        previous_build_stream = record_module._build_recording_stream_runtime
        previous_build_services = record_module._build_recording_session_services
        previous_start_support_threads = record_module._start_recording_session_support_threads
        previous_open_stream = record_module._open_recording_stream
        previous_finalize = record_module._finalize_recording_session
        previous_recording = record_module.bc.recording
        previous_tc_lock = record_module.bc.tc_lock
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
            record_module.pyaudio.PyAudio = lambda: object()

            class ModelRuntimeStub:
                use_temp = True
                cuda_device = "cpu"
                demucs_enabled = False
                hallucination_filters = {}
                stable_tl = None
                whisper_args = {}

            record_module._load_recording_model_runtime = lambda **kwargs: ModelRuntimeStub()

            def fake_build_stream_runtime(*, rec_type, config, p):
                observed["use_temp_seen"] = config.use_temp
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
            record_module._build_recording_session_services = lambda **kwargs: RecordingSessionServices(
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
            record_module._start_recording_session_support_threads = lambda **kwargs: None
            record_module._open_recording_stream = lambda **kwargs: None
            record_module._finalize_recording_session = lambda *args, **kwargs: None
            record_module.bc.recording = False

            record_session("English", "Chinese", "Whisper", "base", "mic", True, False)
        finally:
            record_module._build_recording_session_config = previous_build_config
            record_module.pyaudio.PyAudio = previous_pyaudio
            record_module._load_recording_model_runtime = previous_load_runtime
            record_module._build_recording_stream_runtime = previous_build_stream
            record_module._build_recording_session_services = previous_build_services
            record_module._start_recording_session_support_threads = previous_start_support_threads
            record_module._open_recording_stream = previous_open_stream
            record_module._finalize_recording_session = previous_finalize
            record_module.bc.recording = previous_recording
            record_module.bc.tc_lock = previous_tc_lock

        self.assertTrue(observed["use_temp_seen"])

    def test_record_session_finalizes_when_failure_happens_after_pyaudio_bootstrap(self) -> None:
        from speech_translate.utils.audio import record as record_module

        previous_build_config = record_module._build_recording_session_config
        previous_pyaudio = record_module.pyaudio.PyAudio
        previous_load_runtime = record_module._load_recording_model_runtime
        previous_build_stream = record_module._build_recording_stream_runtime
        previous_finalize = record_module._finalize_recording_session
        previous_empty_cache = record_module.torch.cuda.empty_cache
        previous_recording = record_module.bc.recording
        finalized = []
        try:
            class ConfigStub:
                use_temp = False

            py_audio = object()
            record_module._build_recording_session_config = lambda **kwargs: ConfigStub()
            record_module.pyaudio.PyAudio = lambda: py_audio
            record_module._load_recording_model_runtime = lambda **kwargs: type(
                "ModelRuntime",
                (),
                {"use_temp": False, "cuda_device": "cpu", "demucs_enabled": False},
            )()
            record_module._build_recording_stream_runtime = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
            record_module._finalize_recording_session = lambda *args, **kwargs: finalized.append((args, kwargs))
            record_module.torch.cuda.empty_cache = lambda: None
            record_module.bc.recording = False

            record_session("English", "Chinese", "Whisper", "base", "mic", True, False)
        finally:
            record_module._build_recording_session_config = previous_build_config
            record_module.pyaudio.PyAudio = previous_pyaudio
            record_module._load_recording_model_runtime = previous_load_runtime
            record_module._build_recording_stream_runtime = previous_build_stream
            record_module._finalize_recording_session = previous_finalize
            record_module.torch.cuda.empty_cache = previous_empty_cache
            record_module.bc.recording = previous_recording

        self.assertEqual(finalized[0][0][0], py_audio)
        self.assertIsNone(finalized[0][0][1])
        self.assertIsNone(finalized[0][0][2])
        self.assertTrue(finalized[0][1]["keep_temp"])

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
        )

        self.assertTrue(executed)
        self.assertEqual(translator.calls[-1], ("temp.wav", ""))

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
