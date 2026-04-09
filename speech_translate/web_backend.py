from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from threading import Lock
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from speech_translate.linker import bc, sj


class HeadlessRoot:
    def update(self):
        return None

    def update_idletasks(self):
        return None

    def after(self, _delay: int, callback: Optional[Callable] = None, *args):
        if callback is not None:
            return callback(*args)
        return None

    def winfo_exists(self):
        return True

    def destroy(self):
        return None

    def winfo_rootx(self):
        return 0

    def winfo_rooty(self):
        return 0


class HeadlessLabel:
    def __init__(self, bridge=None):
        self.bridge = bridge
        self.text = ""

    def set_text(self, text: str):
        self.text = text
        if self.bridge is not None:
            self.bridge.update_task_message(text, source="headless-label")

    def configure(self, **kwargs):
        text = kwargs.get("text")
        if text is not None:
            self.set_text(text)


class HeadlessButton:
    def __init__(self):
        self.state = "normal"
        self.command = None

    def configure(self, **kwargs):
        if "state" in kwargs:
            self.state = kwargs["state"]
        if "command" in kwargs:
            self.command = kwargs["command"]


class HeadlessCheckButton:
    def __init__(self):
        self.state = "normal"
        self.selected = False
        self.command = None

    def configure(self, **kwargs):
        if "state" in kwargs:
            self.state = kwargs["state"]
        if "command" in kwargs:
            self.command = kwargs["command"]

    def instate(self, _states):
        return self.selected

    def invoke(self):
        self.selected = not self.selected
        if self.command is not None:
            try:
                self.command()
            except TypeError:
                self.command(self.selected)
        return self.selected


class HeadlessProgressBar:
    def __init__(self, bridge=None):
        self.bridge = bridge
        self.value = 0

    def __setitem__(self, key, value):
        if key == "value":
            self.value = value
            if self.bridge is not None:
                self.bridge.update_task_progress(value, source="headless-progress")

    def __getitem__(self, key):
        if key == "value":
            return self.value
        raise KeyError(key)

    def configure(self, **kwargs):
        return None


class HeadlessQueueWindow:
    def __init__(self, bridge=None):
        self.bridge = bridge
        self.rows: List[List[str]] = []

    def update_sheet(self, rows):
        self.rows = list(rows)
        if self.bridge is not None:
            self.bridge.update_task_rows(self.rows)


@dataclass
class TaskState:
    active: bool = False
    title: str = ""
    message: str = ""
    progress: float = 0.0
    rows: List[List[Any]] = field(default_factory=list)
    error: str = ""
    finished: bool = False


class HeadlessFileProcessDialog:
    def __init__(self, master, title: str, mode: str, headers: List[str], bridge=None):
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

    def destroy(self):
        return None


def headless_mbox(title: str, text: str, style: int = 0, parent=None):
    """Headless replacement for tkinter message boxes used in backend flows."""
    _ = parent
    if style in (2,):
        logger.error(f"[HeadlessMBox] {title}: {text}")
    else:
        logger.info(f"[HeadlessMBox] {title}: {text}")

    # Return True for confirm-style dialogs so batch flows can proceed.
    return True


class HeadlessMainWindow:
    def __init__(self, bridge=None):
        self.bridge = bridge
        self.root = HeadlessRoot()

    def disable_interactions(self):
        return None

    def enable_interactions(self):
        return None

    def tb_clear(self):
        return None

    def start_lb(self, *_args, **_kwargs):
        if self.bridge is not None:
            self.bridge.task_state.active = True

    def stop_lb(self, *_args, **_kwargs):
        if self.bridge is not None:
            self.bridge.task_state.active = False

    def from_file_stop(self, prompt=False, notify=True, master=None):
        return None

    def rec_stop(self):
        return None

    def after_rec_stop(self):
        return None

    def error_notif(self, msg: str, **_kwargs):
        logger.error(msg)
        if self.bridge is not None:
            self.bridge.update_task_error(msg)

    def show(self):
        return None

    def bring_to_front(self):
        return None

    def quit_app(self):
        if self.bridge is not None:
            self.bridge.quit_app()


class WebTaskBridge:
    def __init__(self):
        self.task_state = TaskState()
        self.live_state = {
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
        self._task_message_source = ""
        self._task_progress_source = ""
        self._window = None
        self._tray = None
        self._main_window = HeadlessMainWindow(self)

    def bind_window(self, window):
        self._window = window

    def bind_tray(self, tray):
        self._tray = tray

    def get_window(self):
        return self._window

    def get_tray(self):
        return self._tray

    def _emit_ui_update(self, sections: List[str]):
        window = self._window
        if window is None or not sections:
            return
        try:
            payload = json.dumps({"sections": sections}, ensure_ascii=False)
            window.evaluate_js(
                f"window.dispatchEvent(new CustomEvent('speechtranslate-ui-update', {{ detail: {payload} }}));"
            )
        except Exception:
            pass

    def bind_headless_main_window(self):
        setattr(bc, "mw", self._main_window)
        setattr(bc, "sw", None)
        setattr(bc, "lw", None)
        setattr(bc, "about", None)
        setattr(bc, "ex_tcw", None)
        setattr(bc, "ex_tlw", None)
        return self._main_window

    def reset_task_state(self, title: str = ""):
        with self._lock:
            self.task_state = TaskState(active=True, title=title)
            self._task_message_source = ""
            self._task_progress_source = ""
        self._emit_ui_update(["task"])
        return self.task_state

    def update_task_message(self, message: str, source: str = "general"):
        with self._lock:
            if self.task_state.title == "File Import":
                if self._task_message_source == "progress-log" and source != "progress-log":
                    return
                if source == "progress-log":
                    self._task_message_source = "progress-log"
            self.task_state.message = message
        self._emit_ui_update(["task"])

    def update_task_progress(self, progress: float, source: str = "general"):
        with self._lock:
            incoming = float(progress)
            if self.task_state.title == "File Import":
                if self._task_progress_source == "progress-log" and source != "progress-log":
                    return
                if source == "progress-log":
                    self._task_progress_source = "progress-log"

                # File import receives progress from multiple sources (file-level + model-level),
                # keep it monotonic to avoid regressions/flicker.
                self.task_state.progress = max(float(self.task_state.progress), incoming)
            else:
                self.task_state.progress = incoming
        self._emit_ui_update(["task"])

    def update_task_rows(self, rows):
        with self._lock:
            self.task_state.rows = list(rows)
        self._emit_ui_update(["task"])

    def update_task_error(self, error: str):
        with self._lock:
            self.task_state.error = error
            self.task_state.finished = True
            self.task_state.active = False
            self.task_state.progress = 100.0
        self._emit_ui_update(["task"])

    def finish_task(self, message: str = ""):
        with self._lock:
            self.task_state.message = message
            self.task_state.finished = True
            self.task_state.active = False
            self.task_state.progress = 100.0
        self._emit_ui_update(["task"])

    def snapshot_task_state(self):
        with self._lock:
            return {
                "active": self.task_state.active,
                "title": self.task_state.title,
                "message": self.task_state.message,
                "progress": self.task_state.progress,
                "rows": self.task_state.rows,
                "error": self.task_state.error,
                "finished": self.task_state.finished,
            }

    def _html_to_text(self, html: str) -> str:
        text = html.replace("<br />", "\n").replace("<br/>", "\n").replace("<br>", "\n")
        text = re.sub(r"<[^>]+>", "", text)
        return text.strip()

    def update_live_html(self, target: str, html: str):
        text_target = target.replace("_html", "_text")
        with self._lock:
            if target in self.live_state:
                self.live_state[target] = html
            if text_target in self.live_state:
                self.live_state[text_target] = self._html_to_text(html)
        self._emit_ui_update(["live"])

    def append_live_text(self, target: str, text: str, separator: str = ""):
        key_html = target if target.endswith("_html") else f"{target}_html"
        key_text = key_html.replace("_html", "_text")
        with self._lock:
            old = str(self.live_state.get(key_text, ""))
            combined = f"{old}{text}{separator}"
            self.live_state[key_text] = combined
            escaped = (
                combined.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\n", "<br />")
            )
            self.live_state[key_html] = f"<div>{escaped}</div>"
        self._emit_ui_update(["live"])

    def clear_live(self, prefix: str = ""):
        with self._lock:
            for key in list(self.live_state.keys()):
                if not prefix or key.startswith(prefix):
                    self.live_state[key] = ""
        self._emit_ui_update(["live"])

    def snapshot_live_state(self):
        with self._lock:
            return dict(self.live_state)

    def get_settings_snapshot(self):
        return dict(sj.cache)
