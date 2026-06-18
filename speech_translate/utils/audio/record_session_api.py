from __future__ import annotations

from speech_translate._logging import logger
from speech_translate.runtime_deps import empty_torch_cuda_cache
from speech_translate.utils.audio import record_streaming as streaming_module
from speech_translate.utils.audio.device import get_pyaudio_module
from speech_translate.utils.audio.record_runtime import build_recording_text_state
from speech_translate.utils.audio.record_session_bootstrap import build_recording_session_control
from speech_translate.utils.audio.record_session_orchestrator import (
    build_default_recording_session_execution_hooks,
    resolve_recording_session_execution_hooks,
    run_recording_session,
)
from speech_translate.utils.audio.record_session_pipeline import (
    build_record_callback,
    finalize_recording_session,
    initialize_recording_session_lifecycle,
    open_recording_stream,
    prepare_recording_session_bootstrap,
    recording_settings_snapshot,
    run_recording_session_loop,
    start_recording_session_support_threads,
)
from speech_translate.utils.audio.record_streaming import StreamingStateAdapter
from speech_translate.utils.audio.record_types import RecordingSessionDependencies, RecordingSessionExecutionHooks, RecordingSessionRequest


def _default_recording_session_execution_hooks() -> RecordingSessionExecutionHooks:
    return build_default_recording_session_execution_hooks(
        create_pyaudio_fn=lambda: get_pyaudio_module().PyAudio(),
        prepare_bootstrap_fn=prepare_recording_session_bootstrap,
        initialize_lifecycle_fn=initialize_recording_session_lifecycle,
        start_support_threads_fn=start_recording_session_support_threads,
        open_stream_fn=open_recording_stream,
        run_loop_fn=run_recording_session_loop,
        finalize_session_fn=finalize_recording_session,
        build_record_callback_fn=build_record_callback,
        empty_cuda_cache_fn=empty_torch_cuda_cache,
    )


def _resolve_recording_session_execution_hooks(
    overrides: RecordingSessionExecutionHooks | None,
) -> RecordingSessionExecutionHooks:
    return resolve_recording_session_execution_hooks(
        overrides,
        default_hooks=_default_recording_session_execution_hooks(),
    )


def record_session(
    request: RecordingSessionRequest,
    *,
    dependencies: RecordingSessionDependencies | None = None,
) -> None:
    hooks = _resolve_recording_session_execution_hooks(None if dependencies is None else dependencies.execution_hooks)
    run_recording_session(
        request,
        dependencies=dependencies,
        hooks=hooks,
        recording_settings_snapshot_fn=recording_settings_snapshot,
        build_session_control_fn=build_recording_session_control,
        build_text_state_fn=build_recording_text_state,
        build_callback_context_store_fn=streaming_module.build_callback_context_store,
        stream_state_adapter_factory=StreamingStateAdapter,
        logger_instance=logger,
    )


__all__ = [
    "build_recording_session_control",
    "record_session",
]
