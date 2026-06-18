from __future__ import annotations

from dataclasses import dataclass
from time import time
from typing import Callable, Mapping


NormalizeModelKey = Callable[[str], str]


_WHISPER_MODEL_BYTES: dict[str, int] = {
    "tiny": 75 * 1024 * 1024,
    "tiny.en": 75 * 1024 * 1024,
    "base": 142 * 1024 * 1024,
    "base.en": 142 * 1024 * 1024,
    "small": 466 * 1024 * 1024,
    "small.en": 466 * 1024 * 1024,
    "medium": 1530 * 1024 * 1024,
    "medium.en": 1530 * 1024 * 1024,
    "large-v1": 2900 * 1024 * 1024,
    "large-v2": 2900 * 1024 * 1024,
    "large-v3": 2900 * 1024 * 1024,
}


def estimate_whisper_model_bytes(model_key: str, *, normalize_model_key: NormalizeModelKey) -> int:
    normalized = normalize_model_key(str(model_key))
    if normalized in _WHISPER_MODEL_BYTES:
        return _WHISPER_MODEL_BYTES[normalized]

    if normalized.endswith(".en"):
        return _WHISPER_MODEL_BYTES.get(normalized[:-3], 0)
    return 0


@dataclass
class RuntimeModelStateMachine:
    normalize_model_key: NormalizeModelKey
    key: str = ""
    loaded: bool = False
    loading: bool = False
    message: str = "模型未预加载"
    started_at: float = 0.0

    def set_state(
        self,
        *,
        model_key: str | None = None,
        loaded: bool,
        loading: bool,
        message: str,
    ) -> None:
        if model_key is not None:
            self.key = self.normalize_model_key(str(model_key))
        self.loaded = bool(loaded)
        self.loading = bool(loading)
        self.message = str(message)
        if self.loading:
            if self.started_at <= 0:
                self.started_at = time()
        else:
            self.started_at = 0.0

    def resolve_message(self, normalized_key: str, *, loaded: bool, message: str | None) -> str:
        if message:
            return message
        return f"Model ready: {normalized_key}" if loaded else f"Loading model cache for {normalized_key}"

    def mark_pending(self, model_key: str, *, loaded: bool = False, message: str | None = None) -> None:
        normalized_key = self.normalize_model_key(str(model_key))
        self.set_state(
            model_key=normalized_key,
            loaded=bool(loaded),
            loading=not bool(loaded),
            message=self.resolve_message(
                normalized_key,
                loaded=bool(loaded),
                message=message,
            ),
        )

    def mark_ready(self, model_key: str | None = None, *, message: str | None = None) -> None:
        normalized_key = self.normalize_model_key(str(model_key or self.key))
        self.set_state(
            model_key=normalized_key,
            loaded=True,
            loading=False,
            message=self.resolve_message(
                normalized_key,
                loaded=True,
                message=message,
            ),
        )

    def mark_failed(self, message: str) -> None:
        self.set_state(
            loaded=False,
            loading=False,
            message=str(message),
        )

    def build_state(self) -> dict[str, object]:
        elapsed = 0.0
        if self.loading and not self.loaded and self.started_at > 0:
            elapsed = max(0.0, time() - self.started_at)
        return {
            "key": self.key,
            "loading": self.loading and not self.loaded,
            "loaded": self.loaded,
            "message": self.message,
            "elapsed_seconds": elapsed,
        }

    def handle_task_message(self, message: str, source: str = "general") -> None:
        source_text = str(source or "general").strip().lower()
        text = str(message or "").strip()
        if not text:
            return

        if source_text == "model-download":
            return

        lowered = text.lower()
        if source_text == "model-load":
            if lowered.startswith("preparing model arguments for"):
                self.mark_pending(self.key, message=text)
                return
            if lowered.startswith("checking model cache for"):
                self.mark_pending(self.key, message=text)
                return
            if lowered.startswith("using cached runtime bundle for"):
                self.mark_pending(self.key, message=text)
                return
            if lowered.startswith("loading model into runtime memory for"):
                self.mark_pending(self.key, message=text)
                return
            if lowered.startswith("loading model") or lowered.startswith("loading model cache for"):
                if ":" in text:
                    candidate = text.split(":", 1)[1].strip()
                elif lowered.startswith("loading model cache for "):
                    candidate = text[len("Loading model cache for ") :].strip()
                else:
                    candidate = self.key
                self.mark_pending(candidate or self.key)
                return
            if lowered.startswith("model ready:") or lowered.startswith("model loaded:"):
                candidate = text.split(":", 1)[1].strip() if ":" in text else self.key
                self.mark_ready(candidate or self.key)
                return
            if lowered.startswith("model load failed"):
                self.mark_failed(text)
                return

        if lowered.startswith("loading model and preparing pipeline"):
            if not self.loaded:
                self.mark_pending(self.key)
            else:
                self.mark_ready(self.key)
            return
        if lowered.startswith("loading model:") or lowered.startswith("loading model cache for"):
            candidate = text.split(":", 1)[1].strip() if ":" in text else ""
            next_key = self.normalize_model_key(candidate) if candidate else self.key
            if self.loaded and next_key and self.key == next_key:
                self.mark_ready(self.key)
            else:
                self.mark_pending(next_key)
            return
        if lowered.startswith("model loaded:") or lowered.startswith("model ready:"):
            ready_key = self.normalize_model_key(text.split(":", 1)[1].strip() if ":" in text else self.key)
            self.mark_ready(ready_key)
            return
        if lowered.startswith("model load failed"):
            self.mark_failed(text)

    def handle_recording_status(self, payload: Mapping[str, object]) -> None:
        status_text = str(payload.get("status", "")).lower()
        if "initializing" in status_text:
            if self.key:
                self.mark_pending(self.key)
        elif any(fragment in status_text for fragment in ["recording", "transcrib", "translat"]):
            if self.key:
                self.mark_ready(self.key)
        elif "stopped" in status_text:
            self.loading = False


__all__ = [
    "RuntimeModelStateMachine",
    "estimate_whisper_model_bytes",
]
