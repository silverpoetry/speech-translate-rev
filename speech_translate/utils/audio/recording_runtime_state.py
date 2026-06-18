from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from speech_translate.live_text_service import LiveTextRenderer
from speech_translate.runtime_registry import bridge_state_registry, get_current_bridge


def _get_recording_bridge_state() -> object | None:
    return bridge_state_registry.get()


def _get_recording_runtime_state() -> object:
    bridge = _get_recording_bridge_state()
    if bridge is None:
        raise RuntimeError("recording bridge is not available")
    return bridge.recording_runtime


def _get_recording_text_state() -> object:
    bridge = _get_recording_bridge_state()
    if bridge is None:
        raise RuntimeError("recording bridge is not available")
    return bridge.live_text


@dataclass
class RecordingRuntimeStateAdapter:
    state: object | None = None
    state_provider: Callable[[], object] = _get_recording_runtime_state

    def _state(self) -> object:
        return self.state if self.state is not None else self.state_provider()

    def is_recording_active(self) -> bool:
        return bool(getattr(self._state(), "recording", False))

    def enable_recording(self) -> None:
        setattr(self._state(), "recording", True)

    def disable_recording(self) -> None:
        setattr(self._state(), "recording", False)

    def current_status(self) -> str:
        return str(getattr(self._state(), "current_rec_status", ""))

    def set_current_status(self, status: str) -> None:
        self._state().current_rec_status = status

    def data_queue_empty(self) -> bool:
        return self._state().data_queue.empty()

    def enqueue_audio(self, payload: bytes) -> None:
        self._state().data_queue.put(payload)

    def get_data(self, *, timeout: float) -> bytes:
        return self._state().data_queue.get(timeout=timeout)

    def get_data_nowait(self) -> bytes:
        return self._state().data_queue.get_nowait()

    def clear_data_queue(self) -> None:
        state = self._state()
        while not state.data_queue.empty():
            state.data_queue.get()

    def stream(self):
        return getattr(self._state(), "stream", None)

    def set_stream(self, stream) -> None:
        self._state().stream = stream

    def is_stream_released(self) -> bool:
        return self.stream() is None

    def clear_stream(self) -> None:
        self._state().stream = None

    def clear_runtime_threads(self) -> None:
        state = self._state()
        state.rec_tc_thread = None
        state.rec_tl_thread = None


@dataclass
class RecordingTextStoreAdapter:
    state: object | None = None
    state_provider: Callable[[], object] = _get_recording_text_state
    bridge_getter: Callable[[], object | None] = get_current_bridge
    renderer: LiveTextRenderer | None = None

    def _state(self) -> object:
        return self.state if self.state is not None else self.state_provider()

    def _bridge(self) -> object | None:
        bridge = self.bridge_getter()
        if bridge is not None:
            return bridge
        bridge_state = self._bridge_state()
        visual = getattr(bridge_state, "visual", None) if bridge_state is not None else None
        return getattr(visual, "web_bridge", None) if visual is not None else None

    def _bridge_state(self) -> object | None:
        return _get_recording_bridge_state()

    def _renderer(self) -> LiveTextRenderer | None:
        if self.renderer is not None:
            return self.renderer
        bridge = self._bridge()
        renderer = getattr(bridge, "live_text_renderer", None) if bridge is not None else None
        if renderer is not None:
            return renderer
        return getattr(self._bridge_state(), "live_text_renderer", None)

    def _fg_color(self) -> str:
        bridge = self._bridge()
        visual = getattr(bridge, "visual", None) if bridge is not None else None
        fg_color = str(getattr(visual, "fg_color", "") or "")
        if fg_color:
            return fg_color
        bridge_state_visual = getattr(self._bridge_state(), "visual", None)
        return str(getattr(bridge_state_visual, "fg_color", "") or "")

    def transcribed_sentences(self) -> list[object]:
        return list(getattr(self._state(), "tc_sentences", []))

    def translated_sentences(self) -> list[object]:
        return list(getattr(self._state(), "tl_sentences", []))

    def set_transcribed_sentences(self, sentences: list[object]) -> None:
        self._state().tc_sentences = list(sentences)

    def set_translated_sentences(self, sentences: list[object]) -> None:
        self._state().tl_sentences = list(sentences)

    def append_transcribed_sentence(self, sentence: object) -> None:
        self._state().tc_sentences.append(sentence)

    def append_translated_sentence(self, sentence: object) -> None:
        self._state().tl_sentences.append(sentence)

    def update_transcribed_output(self, current: object | None, separator: str) -> None:
        bridge = self._bridge()
        renderer = self._renderer()
        if bridge is None:
            raise RuntimeError("recording web bridge is not available")
        if renderer is None:
            raise RuntimeError("live text renderer is not available")
        renderer.update_stream(
            bridge,
            mode="tc",
            sentences=self.transcribed_sentences(),
            new_result=current,
            separator=separator,
            fg_color=self._fg_color(),
        )

    def update_translated_output(self, current: object | None, separator: str) -> None:
        bridge = self._bridge()
        renderer = self._renderer()
        if bridge is None:
            raise RuntimeError("recording web bridge is not available")
        if renderer is None:
            raise RuntimeError("live text renderer is not available")
        renderer.update_stream(
            bridge,
            mode="tl",
            sentences=self.translated_sentences(),
            new_result=current,
            separator=separator,
            fg_color=self._fg_color(),
        )

    def detected_language(self) -> str:
        return str(getattr(self._state(), "auto_detected_lang", "~"))

    def set_detected_language(self, language: str) -> None:
        self._state().auto_detected_lang = language


def build_recording_runtime_state_adapter(
    *,
    state: object | None = None,
    state_provider: Callable[[], object] = _get_recording_runtime_state,
) -> RecordingRuntimeStateAdapter:
    return RecordingRuntimeStateAdapter(state=state, state_provider=state_provider)


def build_recording_text_store_adapter(
    *,
    state: object | None = None,
    state_provider: Callable[[], object] = _get_recording_text_state,
    bridge_getter: Callable[[], object | None] = get_current_bridge,
    renderer: LiveTextRenderer | None = None,
) -> RecordingTextStoreAdapter:
    return RecordingTextStoreAdapter(
        state=state,
        state_provider=state_provider,
        bridge_getter=bridge_getter,
        renderer=renderer,
    )


__all__ = [
    "RecordingRuntimeStateAdapter",
    "RecordingTextStoreAdapter",
    "build_recording_runtime_state_adapter",
    "build_recording_text_store_adapter",
]
