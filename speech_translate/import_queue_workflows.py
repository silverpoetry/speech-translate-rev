from __future__ import annotations

from speech_translate.controller_protocols import ModelManagerControllerApi
from speech_translate.import_queue_runtime import ImportQueueProcessRuntime, ImportQueueRuntimeBindings, ImportStartContext


def prepare_runtime_model_for_import(context: ImportStartContext, *, model_manager: ModelManagerControllerApi) -> None:
    if not context.should_prepare_runtime_model:
        return
    if model_manager.is_runtime_model_ready(context.model_name_tc):
        model_manager.mark_runtime_model_ready(context.model_name_tc)
    else:
        model_manager.mark_runtime_model_pending(context.model_name_tc)


def build_import_summary(
    process_runtime: ImportQueueProcessRuntime,
    *,
    is_tc: bool,
    is_tl: bool,
) -> str:
    parts = []
    if is_tc:
        parts.append(f"{process_runtime.transcribed_count()} transcribed")
    if is_tl:
        parts.append(f"{process_runtime.translated_count()} translated")
    return ", ".join(parts) or "no output generated"


def build_file_process_dependencies(*, context: ImportStartContext, runtime_bindings: ImportQueueRuntimeBindings, bridge):
    from speech_translate.utils.audio import file_api as audio_file_module

    return audio_file_module.FileProcessDependencies(
        ui_bridge=audio_file_module.build_file_ui_bridge_adapter(bridge=bridge),
        result_queue=audio_file_module.build_file_result_queue_adapter(
            state=runtime_bindings.recording_state,
            state_provider=None,
        ),
        processing_state=audio_file_module.build_file_processing_state_adapter(
            state=runtime_bindings.file_state,
            state_provider=None,
        ),
        settings=audio_file_module.FileSettingsAdapter(cache=dict(context.settings_snapshot)),
        environment=audio_file_module.build_file_environment_adapter(
            visual_state=runtime_bindings.visual_state,
            visual_state_provider=None,
        ),
    )


def build_file_process_request(context: ImportStartContext):
    from speech_translate.utils.audio import file_api as audio_file_module

    return audio_file_module.FileProcessRequest(
        data_files=context.files_to_process,
        model_name_tc=context.model_name_tc,
        lang_source=str(context.settings_snapshot.get("source_lang_f_import", "English")),
        lang_target=str(context.settings_snapshot.get("target_lang_f_import", "Indonesian")),
        is_tc=context.is_tc,
        is_tl=context.is_tl,
        engine=context.engine,
    )


__all__ = [
    "build_file_process_dependencies",
    "build_file_process_request",
    "build_import_summary",
    "prepare_runtime_model_for_import",
]
