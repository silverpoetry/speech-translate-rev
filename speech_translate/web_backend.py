from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from threading import Lock
from typing import Callable, Optional, Sequence

from loguru import logger

from speech_translate.controller_protocols import JsonDict, TaskTable, TaskTableRow, TrayLike, WebviewWindowLike
from speech_translate.linker import bc, sj
from speech_translate.ui_protocol import (
    TASK_SOURCE_HEADLESS_LABEL,
    TASK_SOURCE_HEADLESS_PROGRESS,
    TASK_SOURCE_GENERAL,
    UI_EVENT_NAME,
    UI_SECTION_LIVE,
    UI_SECTION_TASK,
)


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


class HeadlessLabel:
    def __init__(self, bridge: WebTaskBridge | None = None):
        self.bridge = bridge
        self.text = ""

    def set_text(self, text: str) -> None:
        self.text = text
        if self.bridge is not None:
            self.bridge.update_task_message(text, source=TASK_SOURCE_HEADLESS_LABEL)

    def configure(self, **kwargs: object) -> None:
        text = kwargs.get("text")
        if text is not None:
            self.set_text(str(text))


class HeadlessButton:
    def __init__(self):
        self.state = "normal"
        self.command: Callable[..., object] | None = None

    def configure(self, **kwargs: object) -> None:
        if "state" in kwargs:
            self.state = str(kwargs["state"])
        if "command" in kwargs:
            command = kwargs["command"]
            self.command = command if callable(command) else None


class HeadlessCheckButton:
    def __init__(self):
        self.state = "normal"
        self.selected = False
        self.command: Callable[..., object] | None = None

    def configure(self, **kwargs: object) -> None:
        if "state" in kwargs:
            self.state = str(kwargs["state"])
        if "command" in kwargs:
            command = kwargs["command"]
            self.command = command if callable(command) else None

    def instate(self, _states: Sequence[str]) -> bool:
        return self.selected

    def invoke(self) -> bool:
        self.selected = not self.selected
        if self.command is not None:
            try:
                self.command()
            except TypeError:
                self.command(self.selected)
        return self.selected


class HeadlessProgressBar:
    def __init__(self, bridge: WebTaskBridge | None = None):
        self.bridge = bridge
        self.value = 0.0

    def __setitem__(self, key: str, value: object) -> None:
        if key == "value":
            self.value = float(value)
            if self.bridge is not None:
                self.bridge.update_task_progress(self.value, source=TASK_SOURCE_HEADLESS_PROGRESS)

    def __getitem__(self, key: str) -> float:
        if key == "value":
            return self.value
        raise KeyError(key)

    def configure(self, **kwargs: object) -> None:
        return None


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


class HeadlessFileProcessDialog:
    def __init__(self, master: object, title: str, mode: str, headers: list[str], bridge: WebTaskBridge | None = None):
        self.bridge = bridge
        self.mode = mode
        self.headers = headers
        self.root = HeadlessRoot()
        self.lbl_task_name = HeadlessLabel(bridge)
        self.lbl_elapsed = HeadlessLabel(bridge)
        self.lbl_files = HeadlessLabel(bridge)
        self.lbl_processed = HeadlessLabel(bridge)
        self.cbtn_open_folder = HeadlessCheckButton()
        self.btn_add = HeadlessButton()
        self.btn_cancel = HeadlessButton()
        self.progress_bar = HeadlessProgressBar(bridge)
        self.queue_window = HeadlessQueueWindow(bridge)
        self.task_title = title

    def destroy(self) -> None:
        return None


def headless_mbox(title: str, text: str, style: int = 0, parent: object | None = None) -> bool:
    """Headless replacement for tkinter message boxes used in backend flows."""
    _ = parent
    if style in (2,):
        logger.error(f"[HeadlessMBox] {title}: {text}")
    else:
        logger.info(f"[HeadlessMBox] {title}: {text}")

    # Return True for confirm-style dialogs so batch flows can proceed.
    return True


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
            self.bridge.task_state.active = True

    def stop_lb(self, *_args: object, **_kwargs: object) -> None:
        if self.bridge is not None:
            self.bridge.task_state.active = False

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
        self._emit_ui_update([UI_SECTION_TASK])
        return self.task_state

    def update_task_message(self, message: str, source: str = TASK_SOURCE_GENERAL) -> None:
        with self._lock:
            self.task_state.message = message
            self.task_state.message_source = source
        self._emit_ui_update([UI_SECTION_TASK])

    def update_task_progress(self, progress: float, source: str = TASK_SOURCE_GENERAL) -> None:
        with self._lock:
            incoming = float(progress)
            if self.task_state.title == "File Import":
                # file-import receives progress from multiple sources; keep monotonic to avoid regressions/flicker.
                self.task_state.progress = max(float(self.task_state.progress), incoming)
            else:
                self.task_state.progress = incoming
            self.task_state.progress_source = source
        self._emit_ui_update([UI_SECTION_TASK])

    def update_task_rows(self, rows: Sequence[Sequence[object]]) -> None:
        with self._lock:
            self.task_state.rows = self._normalize_task_rows(rows)
        self._emit_ui_update([UI_SECTION_TASK])

    def update_task_error(self, error: str) -> None:
        with self._lock:
            self.task_state.error = error
            self.task_state.finished = True
            self.task_state.active = False
            self.task_state.progress = 100.0
        self._emit_ui_update([UI_SECTION_TASK])

    def finish_task(self, message: str = "") -> None:
        with self._lock:
            self.task_state.message = message
            self.task_state.finished = True
            self.task_state.active = False
            self.task_state.progress = 100.0
        self._emit_ui_update([UI_SECTION_TASK])

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
        normalized: TaskTable = []
        for row in rows:
            normalized.append([cell for cell in row])
        return normalized

    def _html_to_text(self, html: str) -> str:
        text = html.replace("<br />", "\n").replace("<br/>", "\n").replace("<br>", "\n")
        text = text.replace("</span>", "\n")
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\n{2,}", "\n", text)
        return text.strip()

    def update_live_html(self, target: str, html: str) -> None:
        text_target = target.replace("_html", "_text")
        with self._lock:
            if target in self.live_state:
                self.live_state[target] = html
            if text_target in self.live_state:
                self.live_state[text_target] = self._html_to_text(html)
        self._emit_ui_update([UI_SECTION_LIVE])

    def append_live_text(self, target: str, text: str, separator: str = "") -> None:
        key_html = target if target.endswith("_html") else f"{target}_html"
        key_text = key_html.replace("_html", "_text")
        with self._lock:
            old = str(self.live_state.get(key_text, ""))
            combined = f"{old}{text}{separator}"
            self.live_state[key_text] = combined
            lines = [line for line in combined.splitlines() if line != ""]
            if not lines:
                lines = [""]

            escaped_lines = []
            for line in lines:
                escaped_line = (
                    line.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                )
                escaped_lines.append(f"<span class='live-line'>{escaped_line}</span>")

            self.live_state[key_html] = "<div class='live-lines'>" + "<br />".join(escaped_lines) + "</div>"
        self._emit_ui_update([UI_SECTION_LIVE])

    def clear_live(self, prefix: str = "") -> None:
        with self._lock:
            for key in list(self.live_state.keys()):
                if not prefix or key.startswith(prefix):
                    self.live_state[key] = ""
        self._emit_ui_update([UI_SECTION_LIVE])

    def snapshot_live_state(self) -> JsonDict:
        with self._lock:
            return dict(self.live_state)

    def get_settings_snapshot(self) -> JsonDict:
        return dict(sj.cache)
