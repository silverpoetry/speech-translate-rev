from __future__ import annotations

from time import time
from typing import Callable

from speech_translate.utils.audio.record_types import (
    RecordingSessionDependencies,
    RecordingSessionExecutionHooks,
    RecordingSessionFinalizeContext,
    RecordingSessionRequest,
    RealtimeSharedState,
)


def build_default_recording_session_execution_hooks(
    *,
    create_pyaudio_fn: Callable[[], object],
    prepare_bootstrap_fn: Callable[..., object],
    initialize_lifecycle_fn: Callable[..., object],
    start_support_threads_fn: Callable[..., None],
    open_stream_fn: Callable[..., None],
    run_loop_fn: Callable[..., None],
    finalize_session_fn: Callable[..., None],
    build_record_callback_fn: Callable[..., Callable[..., object]],
    empty_cuda_cache_fn: Callable[[], None],
) -> RecordingSessionExecutionHooks:
    return RecordingSessionExecutionHooks(
        create_pyaudio_fn=create_pyaudio_fn,
        prepare_bootstrap_fn=prepare_bootstrap_fn,
        initialize_lifecycle_fn=initialize_lifecycle_fn,
        start_support_threads_fn=start_support_threads_fn,
        open_stream_fn=open_stream_fn,
        run_loop_fn=run_loop_fn,
        finalize_session_fn=finalize_session_fn,
        build_record_callback_fn=build_record_callback_fn,
        empty_cuda_cache_fn=empty_cuda_cache_fn,
    )


def resolve_recording_session_execution_hooks(
    overrides: RecordingSessionExecutionHooks | None,
    *,
    default_hooks: RecordingSessionExecutionHooks,
) -> RecordingSessionExecutionHooks:
    if overrides is None:
        return default_hooks

    return RecordingSessionExecutionHooks(
        create_pyaudio_fn=overrides.create_pyaudio_fn or default_hooks.create_pyaudio_fn,
        prepare_bootstrap_fn=overrides.prepare_bootstrap_fn or default_hooks.prepare_bootstrap_fn,
        initialize_lifecycle_fn=overrides.initialize_lifecycle_fn or default_hooks.initialize_lifecycle_fn,
        start_support_threads_fn=overrides.start_support_threads_fn or default_hooks.start_support_threads_fn,
        open_stream_fn=overrides.open_stream_fn or default_hooks.open_stream_fn,
        run_loop_fn=overrides.run_loop_fn or default_hooks.run_loop_fn,
        finalize_session_fn=overrides.finalize_session_fn or default_hooks.finalize_session_fn,
        build_record_callback_fn=overrides.build_record_callback_fn or default_hooks.build_record_callback_fn,
        empty_cuda_cache_fn=overrides.empty_cuda_cache_fn or default_hooks.empty_cuda_cache_fn,
    )


def run_recording_session(
    request: RecordingSessionRequest,
    *,
    dependencies: RecordingSessionDependencies | None,
    hooks: RecordingSessionExecutionHooks,
    recording_settings_snapshot_fn: Callable[[object | None], object],
    build_session_control_fn: Callable[[], object],
    build_text_state_fn: Callable[[], object],
    build_callback_context_store_fn: Callable[[], object],
    stream_state_adapter_factory: Callable[..., object],
    logger_instance,
) -> None:
    rec_type = request.rec_type
    p = None
    lifecycle = None
    settings_snapshot = dict(
        recording_settings_snapshot_fn(
            None if dependencies is None or dependencies.settings_snapshot is None else dependencies.settings_snapshot
        )
    )
    session_control = (
        build_session_control_fn()
        if dependencies is None or dependencies.session_control is None
        else dependencies.session_control
    )
    session_text_state = (
        build_text_state_fn()
        if dependencies is None or dependencies.runtime_text_state is None
        else dependencies.runtime_text_state
    )
    session_shared_state = getattr(session_text_state, "_shared", RealtimeSharedState())
    session_callback_context_store = (
        build_callback_context_store_fn()
        if dependencies is None or dependencies.callback_context_store is None
        else dependencies.callback_context_store
    )

    try:
        if hooks.create_pyaudio_fn is None:
            raise RuntimeError("Recording session hook create_pyaudio_fn is required")
        if hooks.prepare_bootstrap_fn is None:
            raise RuntimeError("Recording session hook prepare_bootstrap_fn is required")
        if hooks.initialize_lifecycle_fn is None:
            raise RuntimeError("Recording session hook initialize_lifecycle_fn is required")
        if hooks.start_support_threads_fn is None:
            raise RuntimeError("Recording session hook start_support_threads_fn is required")
        if hooks.open_stream_fn is None:
            raise RuntimeError("Recording session hook open_stream_fn is required")
        if hooks.run_loop_fn is None:
            raise RuntimeError("Recording session hook run_loop_fn is required")
        if hooks.build_record_callback_fn is None:
            raise RuntimeError("Recording session hook build_record_callback_fn is required")

        p = hooks.create_pyaudio_fn()
        bootstrap = hooks.prepare_bootstrap_fn(
            rec_type=rec_type,
            settings_snapshot=settings_snapshot,
            lang_source=request.lang_source,
            engine=request.engine,
            model_name_tc=request.model_name_tc,
            is_tc=request.is_tc,
            is_tl=request.is_tl,
            p=p,
            shared_runtime_state=session_shared_state,
            callback_context_store_instance=session_callback_context_store,
        )
        config = bootstrap.config
        model_runtime = bootstrap.model_runtime
        stream_runtime = bootstrap.stream_runtime

        logger_instance.info(
            f"Session starting: {config.taskname} | Engine: {request.engine} | Device: {model_runtime.cuda_device} | Demucs: {model_runtime.demucs_enabled}"
        )

        t_start = time()
        lifecycle = hooks.initialize_lifecycle_fn(
            config=config,
            model_runtime=model_runtime,
            stream_runtime=stream_runtime,
            device=request.device,
            lang_source=request.lang_source,
            lang_target=request.lang_target,
            engine=request.engine,
            is_tc=request.is_tc,
            is_tl=request.is_tl,
            t_start=t_start,
            control=session_control,
            runtime_text_state=session_text_state,
        )
        hooks.start_support_threads_fn(
            services=lifecycle.services,
            session_state=lifecycle.session_state,
            t_start=t_start,
            max_buffer_s=config.max_buffer_s,
            max_sentences=config.max_sentences,
            sentence_limitless=config.sentence_limitless,
            control=session_control,
            runtime_text_state=session_text_state,
        )

        stream_state_adapter = stream_state_adapter_factory(runtime_state=session_control.runtime_state)
        hooks.open_stream_fn(
            p=p,
            stream_runtime=stream_runtime,
            record_cb_override=hooks.build_record_callback_fn(
                stream_runtime.callback_ctx,
                state_adapter=stream_state_adapter,
            ),
            state_adapter=stream_state_adapter,
        )

        hooks.run_loop_fn(
            lifecycle=lifecycle,
            config=config,
            model_runtime=model_runtime,
            is_tc=request.is_tc,
            is_tl=request.is_tl,
            rec_type=rec_type,
            control=session_control,
            runtime_text_state=session_text_state,
        )
    except Exception as exc:
        logger_instance.error(f"Error in record session: {str(exc)}")
    finally:
        if p is not None:
            try:
                finalize_context = RecordingSessionFinalizeContext.from_lifecycle(lifecycle)
                if hooks.finalize_session_fn is None:
                    raise RuntimeError("Recording session hook finalize_session_fn is required")
                hooks.finalize_session_fn(p, finalize_context, control=session_control)
            except Exception as finalize_exc:
                logger_instance.error(f"Error finalizing record session: {finalize_exc}")
        session_callback_context_store.reset()
        if hooks.empty_cuda_cache_fn is None:
            raise RuntimeError("Recording session hook empty_cuda_cache_fn is required")
        hooks.empty_cuda_cache_fn()
        logger_instance.info("Record session ended")
