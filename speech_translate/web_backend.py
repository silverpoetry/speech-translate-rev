from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
import json
import re
from threading import Lock
from typing import Callable, Optional, Sequence

from speech_translate.controller_protocols import JsonDict, TaskTable, TaskTableRow, TrayLike, WebviewWindowLike
from speech_translate.linker import bc, sj
from speech_translate.log_helpers import logger
from speech_translate.ui_protocol import TASK_SOURCE_GENERAL, UI_EVENT_NAME, UI_SECTION_LIVE, UI_SECTION_TASK


class HeadlessRoot:
    def update(self) -> None:
        return None

    def update_idletasks(self) -> None:
        return None

    def after(self, _delay: int, callback: Optional[Callable[..., object]] = None, *args: object) -> object | None:
        if callback is not None:
            return callback(*args)
        return None

    def winfo_exists(self) -> bool:
        return True

    def destroy(self) -> None:
        return None

    def winfo_rootx(self) -> int:
        return 0

    def winfo_rooty(self) -> int:
        return 0


class HeadlessQueueWindow:
    def __init__(self, bridge: WebTaskBridge | None = None):
        self.bridge = bridge
        self.rows: TaskTable = []

    def update_sheet(self, rows: Sequence[Sequence[object]]) -> None:
        self.rows = [list(row) for row in rows]
        if self.bridge is not None:
            self.bridge.update_task_rows(self.rows)


@dataclass
class TaskState:
    active: bool = False
    title: str = ""
    message: str = ""
    progress: float = 0.0
    rows: TaskTable = field(default_factory=list)
    error: str = ""
    finished: bool = False
    message_source: str = ""
    progress_source: str = ""


class HeadlessMainWindow:
    def __init__(self, bridge: WebTaskBridge | None = None):
        self.bridge = bridge
        self.root = HeadlessRoot()

    def disable_interactions(self) -> None:
        return None

    def enable_interactions(self) -> None:
        return None

    def tb_clear(self) -> None:
        return None

    def start_lb(self, *_args: object, **_kwargs: object) -> None:
        if self.bridge is not None:
            self.bridge.set_task_active(True)

    def stop_lb(self, *_args: object, **_kwargs: object) -> None:
        if self.bridge is not None:
            self.bridge.set_task_active(False)

    def from_file_stop(self, prompt: bool = False, notify: bool = True, master: object | None = None) -> None:
        _ = (prompt, notify, master)
        return None

    def rec_stop(self) -> None:
        return None

    def after_rec_stop(self) -> None:
        return None

    def error_notif(self, msg: str, **_kwargs):
        logger.error(msg)
        if self.bridge is not None:
            self.bridge.update_task_error(msg)

    def show(self) -> None:
        return None

    def bring_to_front(self) -> None:
        return None

    def quit_app(self) -> None:
        if self.bridge is not None:
            self.bridge.quit_app()


class WebTaskBridge:
    def __init__(self):
        self.task_state = TaskState()
        self.live_state: JsonDict = {
            "main_transcribed_html": "",
            "main_translated_html": "",
            "detached_transcribed_html": "",
            "detached_translated_html": "",
            "main_transcribed_text": "",
            "main_translated_text": "",
            "detached_transcribed_text": "",
            "detached_translated_text": "",
        }
        self._lock = Lock()
        self._window: WebviewWindowLike | None = None
        self._tray: TrayLike | None = None
        self._main_window = HeadlessMainWindow(self)

    def _emit_task_update(self) -> None:
        self._emit_ui_update([UI_SECTION_TASK])

    def _emit_live_update(self) -> None:
        self._emit_ui_update([UI_SECTION_LIVE])

    def _resolve_live_targets(self, target: str) -> tuple[str, str]:
        key_html = target if target.endswith("_html") else f"{target}_html"
        return key_html, key_html.replace("_html", "_text")

    def _set_task_state_fields(self, **updates: object) -> None:
        with self._lock:
            for key, value in updates.items():
                setattr(self.task_state, key, value)

    def _complete_task(self, *, message: str = "", error: str = "") -> None:
        with self._lock:
            if message:
                self.task_state.message = message
            if error:
                self.task_state.error = error
            self.task_state.finished = True
            self.task_state.active = False
            self.task_state.progress = 100.0

    def _normalize_live_text(self, html: str) -> str:
        text = html.replace("<br />", "\n").replace("<br/>", "\n").replace("<br>", "\n")
        text = text.replace("</span>", "\n")
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\n{2,}", "\n", text)
        return text.strip()

    def _render_live_html(self, text: str) -> str:
        lines = [line for line in text.splitlines() if line != ""]
        if not lines:
            lines = [""]
        escaped_lines = [f"<span class='live-line'>{escape(line)}</span>" for line in lines]
        return "<div class='live-lines'>" + "<br />".join(escaped_lines) + "</div>"

    def _set_live_content(self, html_key: str, text_key: str, *, html: str, text: str) -> None:
        if html_key in self.live_state:
            self.live_state[html_key] = html
        if text_key in self.live_state:
            self.live_state[text_key] = text

    def bind_window(self, window: WebviewWindowLike) -> None:
        self._window = window

    def bind_tray(self, tray: TrayLike) -> None:
        self._tray = tray

    def get_window(self) -> WebviewWindowLike | None:
        return self._window

    def get_tray(self) -> TrayLike | None:
        return self._tray

    def _emit_ui_update(self, sections: list[str]) -> None:
        window = self._window
        if window is None or not sections:
            return
        try:
            payload = json.dumps({"sections": sections}, ensure_ascii=False)
            window.evaluate_js(
                f"window.dispatchEvent(new CustomEvent('{UI_EVENT_NAME}', {{ detail: {payload} }}));"
            )
        except Exception:
            pass

    def bind_headless_main_window(self) -> HeadlessMainWindow:
        setattr(bc, "mw", self._main_window)
        setattr(bc, "sw", None)
        setattr(bc, "lw", None)
        setattr(bc, "about", None)
        setattr(bc, "ex_tcw", None)
        setattr(bc, "ex_tlw", None)
        return self._main_window

    def reset_task_state(self, title: str = "") -> TaskState:
        with self._lock:
            self.task_state = TaskState(active=True, title=title)
        self._emit_task_update()
        return self.task_state

    def update_task_message(self, message: str, source: str = TASK_SOURCE_GENERAL) -> None:
        self._set_task_state_fields(message=message, message_source=source)
        self._emit_task_update()

    def update_task_progress(self, progress: float, source: str = TASK_SOURCE_GENERAL) -> None:
        with self._lock:
            incoming = float(progress)
            if self.task_state.title == "File Import":
                # file-import receives progress from multiple sources; keep monotonic to avoid regressions/flicker.
                self.task_state.progress = max(float(self.task_state.progress), incoming)
            else:
                self.task_state.progress = incoming
            self.task_state.progress_source = source
        self._emit_task_update()

    def update_task_rows(self, rows: Sequence[Sequence[object]]) -> None:
        self._set_task_state_fields(rows=self._normalize_task_rows(rows))
        self._emit_task_update()

    def update_task_error(self, error: str) -> None:
        self._complete_task(error=error)
        self._emit_task_update()

    def finish_task(self, message: str = "") -> None:
        self._complete_task(message=message)
        self._emit_task_update()

    def set_task_active(self, active: bool) -> None:
        self._set_task_state_fields(active=bool(active))
        self._emit_task_update()

    def snapshot_task_state(self) -> JsonDict:
        with self._lock:
            return {
                "active": self.task_state.active,
                "title": self.task_state.title,
                "message": self.task_state.message,
                "progress": self.task_state.progress,
                "rows": [list(row) for row in self.task_state.rows],
                "error": self.task_state.error,
                "finished": self.task_state.finished,
                "message_source": self.task_state.message_source,
                "progress_source": self.task_state.progress_source,
            }

    def _normalize_task_rows(self, rows: Sequence[Sequence[object]]) -> TaskTable:
        return [[cell for cell in row] for row in rows]

    def update_live_html(self, target: str, html: str) -> None:
        html_target, text_target = self._resolve_live_targets(target)
        with self._lock:
            self._set_live_content(
                html_target,
                text_target,
                html=html,
                text=self._normalize_live_text(html),
            )
        self._emit_live_update()

    def append_live_text(self, target: str, text: str, separator: str = "") -> None:
        key_html, key_text = self._resolve_live_targets(target)
        with self._lock:
            old = str(self.live_state.get(key_text, ""))
            combined = f"{old}{text}{separator}"
            self._set_live_content(key_html, key_text, html=self._render_live_html(combined), text=combined)
        self._emit_live_update()

    def clear_live(self, prefix: str = "") -> None:
        with self._lock:
            for key in list(self.live_state.keys()):
                if not prefix or key.startswith(prefix):
                    self.live_state[key] = ""
        self._emit_live_update()

    def snapshot_live_state(self) -> JsonDict:
        with self._lock:
            return dict(self.live_state)

    def get_settings_snapshot(self) -> JsonDict:
        return dict(sj.cache)
