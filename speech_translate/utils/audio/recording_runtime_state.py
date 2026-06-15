from __future__ import annotations

from dataclasses import dataclass, field

from speech_translate.linker import bc


@dataclass
class RecordingRuntimeStateAdapter:
    state: object = field(default_factory=lambda: bc)

    def is_recording_active(self) -> bool:
        return bool(getattr(self.state, "recording", False))

    def enable_recording(self) -> None:
        self.state.enable_rec()

    def disable_recording(self) -> None:
        self.state.disable_rec()

    def current_status(self) -> str:
        return str(getattr(self.state, "current_rec_status", ""))

    def set_current_status(self, status: str) -> None:
        self.state.current_rec_status = status

    def data_queue_empty(self) -> bool:
        return self.state.data_queue.empty()

    def enqueue_audio(self, payload: bytes) -> None:
        self.state.data_queue.put(payload)

    def get_data(self, *, timeout: float) -> bytes:
        return self.state.data_queue.get(timeout=timeout)

    def get_data_nowait(self) -> bytes:
        return self.state.data_queue.get_nowait()

    def clear_data_queue(self) -> None:
        while not self.state.data_queue.empty():
            self.state.data_queue.get()

    def stream(self):
        return getattr(self.state, "stream", None)

    def set_stream(self, stream) -> None:
        self.state.stream = stream

    def is_stream_released(self) -> bool:
        return self.stream() is None

    def clear_stream(self) -> None:
        self.state.stream = None

    def clear_runtime_threads(self) -> None:
        self.state.rec_tc_thread = None
        self.state.rec_tl_thread = None


@dataclass
class RecordingTextStoreAdapter:
    state: object = field(default_factory=lambda: bc)

    def transcribed_sentences(self) -> list[object]:
        return list(getattr(self.state, "tc_sentences", []))

    def translated_sentences(self) -> list[object]:
        return list(getattr(self.state, "tl_sentences", []))

    def set_transcribed_sentences(self, sentences: list[object]) -> None:
        self.state.tc_sentences = list(sentences)

    def set_translated_sentences(self, sentences: list[object]) -> None:
        self.state.tl_sentences = list(sentences)

    def append_transcribed_sentence(self, sentence: object) -> None:
        self.state.tc_sentences.append(sentence)

    def append_translated_sentence(self, sentence: object) -> None:
        self.state.tl_sentences.append(sentence)

    def update_transcribed_output(self, current: object | None, separator: str) -> None:
        self.state.update_tc(current, separator)

    def update_translated_output(self, current: object | None, separator: str) -> None:
        self.state.update_tl(current, separator)

    def detected_language(self) -> str:
        return str(getattr(self.state, "auto_detected_lang", "~"))

    def set_detected_language(self, language: str) -> None:
        self.state.auto_detected_lang = language


recording_runtime_state = RecordingRuntimeStateAdapter()
recording_text_store = RecordingTextStoreAdapter()


__all__ = [
    "RecordingRuntimeStateAdapter",
    "RecordingTextStoreAdapter",
    "recording_runtime_state",
    "recording_text_store",
]
