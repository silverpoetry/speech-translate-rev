from __future__ import annotations

import ctypes
from threading import Event, Lock, Thread

from speech_translate._path import p_app_icon
from speech_translate.controller_protocols import AppTrayBridge
from speech_translate.log_helpers import logger


class NativeTrayPanelHost:
    WIDTH = 308
    HEIGHT = 260

    def __init__(self, tray: "AppTray"):
        self._tray = tray
        self._thread: Thread | None = None
        self._ready = Event()
        self._lock = Lock()
        self._stopping = False
        self._form = None
        self._context = None
        self._action_type = None

    def _run(self) -> None:
        try:
            import clr

            clr.AddReference("System.Windows.Forms")
            clr.AddReference("System.Drawing")

            from System import Action
            from System.Drawing import Color, Font, FontStyle, Point, Size
            import System.Windows.Forms as WinForms

            self._action_type = Action
            self._context = WinForms.ApplicationContext()

            form = WinForms.Form()
            form.Text = "Speech Translate"
            form.ShowInTaskbar = False
            form.TopMost = True
            form.StartPosition = WinForms.FormStartPosition.Manual
            form.FormBorderStyle = getattr(WinForms.FormBorderStyle, "None")
            form.BackColor = Color.FromArgb(245, 247, 250)
            form.ForeColor = Color.FromArgb(22, 27, 34)
            form.ClientSize = Size(self.WIDTH, self.HEIGHT)
            form.AutoScaleMode = WinForms.AutoScaleMode.Dpi
            form.KeyPreview = True

            shell = WinForms.Panel()
            shell.Dock = WinForms.DockStyle.Fill
            shell.BackColor = Color.FromArgb(248, 249, 251)
            shell.Padding = WinForms.Padding(14)
            form.Controls.Add(shell)

            header = WinForms.Panel()
            header.Dock = WinForms.DockStyle.Top
            header.Height = 58
            header.BackColor = Color.FromArgb(248, 249, 251)
            shell.Controls.Add(header)

            title = WinForms.Label()
            title.Text = "Speech Translate"
            title.Font = Font("Segoe UI", 11.0, FontStyle.Bold)
            title.ForeColor = Color.FromArgb(15, 23, 42)
            title.AutoSize = False
            title.Location = Point(0, 0)
            title.Size = Size(220, 24)
            header.Controls.Add(title)

            subtitle = WinForms.Label()
            subtitle.Text = "托盘快捷操作"
            subtitle.Font = Font("Segoe UI", 8.8, FontStyle.Regular)
            subtitle.ForeColor = Color.FromArgb(100, 116, 139)
            subtitle.AutoSize = False
            subtitle.Location = Point(0, 26)
            subtitle.Size = Size(180, 18)
            header.Controls.Add(subtitle)

            close_button = WinForms.Button()
            close_button.Text = "×"
            close_button.FlatStyle = WinForms.FlatStyle.Flat
            close_button.FlatAppearance.BorderSize = 0
            close_button.BackColor = Color.FromArgb(233, 236, 240)
            close_button.ForeColor = Color.FromArgb(51, 65, 85)
            close_button.Font = Font("Segoe UI", 11.0, FontStyle.Bold)
            close_button.Size = Size(34, 30)
            close_button.Location = Point(self.WIDTH - 62, 0)
            close_button.Cursor = WinForms.Cursors.Hand
            close_button.Click += lambda *_: form.Hide()
            header.Controls.Add(close_button)

            action_stack = WinForms.FlowLayoutPanel()
            action_stack.Dock = WinForms.DockStyle.Fill
            action_stack.FlowDirection = WinForms.FlowDirection.TopDown
            action_stack.WrapContents = False
            action_stack.AutoScroll = False
            action_stack.BackColor = Color.FromArgb(248, 249, 251)
            shell.Controls.Add(action_stack)

            def run_action(callback):
                form.Hide()
                Thread(target=callback, daemon=True, name="TrayPanelAction").start()

            def build_button(text: str, callback, *, primary: bool = False, danger: bool = False):
                button = WinForms.Button()
                button.Text = text
                button.Width = self.WIDTH - 32
                button.Height = 36
                button.Margin = WinForms.Padding(0, 0, 0, 8)
                button.FlatStyle = WinForms.FlatStyle.Flat
                button.FlatAppearance.BorderSize = 0
                button.Cursor = WinForms.Cursors.Hand
                button.Font = Font("Segoe UI", 9.4, FontStyle.Regular)
                if danger:
                    button.BackColor = Color.FromArgb(255, 236, 232)
                    button.ForeColor = Color.FromArgb(185, 28, 28)
                elif primary:
                    button.BackColor = Color.FromArgb(225, 236, 255)
                    button.ForeColor = Color.FromArgb(30, 64, 175)
                else:
                    button.BackColor = Color.FromArgb(236, 240, 244)
                    button.ForeColor = Color.FromArgb(30, 41, 59)
                button.Click += lambda *_: run_action(callback)
                return button

            action_stack.Controls.Add(build_button("显示主窗口", self._tray.show_app, primary=True))
            action_stack.Controls.Add(build_button("模型目录", lambda: self._tray.bridge.open_directory("model")))
            action_stack.Controls.Add(build_button("导出目录", lambda: self._tray.bridge.open_directory("export")))
            action_stack.Controls.Add(build_button("日志目录", lambda: self._tray.bridge.open_directory("log")))
            action_stack.Controls.Add(build_button("退出程序", self._tray.exit_app, danger=True))

            def on_form_closing(_sender, args):
                if self._stopping:
                    return
                args.Cancel = True
                form.Hide()

            def on_deactivate(_sender, _args):
                if not self._stopping:
                    form.Hide()

            def on_key_down(_sender, args):
                if int(args.KeyCode) == int(WinForms.Keys.Escape):
                    form.Hide()

            form.FormClosing += on_form_closing
            form.Deactivate += on_deactivate
            form.KeyDown += on_key_down

            self._form = form
            self._ready.set()
            WinForms.Application.Run(self._context)
        except Exception:
            logger.exception("Failed to initialize native tray panel")
            self._ready.set()
        finally:
            self._form = None
            self._context = None
            self._action_type = None

    def ensure_started(self) -> bool:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._stopping = False
                self._ready.clear()
                self._thread = Thread(target=self._run, daemon=True, name="TrayPanelUI")
                self._thread.start()
        self._ready.wait(5)
        return self._form is not None

    def _invoke(self, callback) -> bool:
        form = self._form
        action_type = self._action_type
        if form is None or action_type is None:
            return False
        try:
            if form.InvokeRequired:
                form.Invoke(action_type(callback))
            else:
                callback()
            return True
        except Exception:
            logger.exception("Failed to invoke tray panel callback")
            return False

    def show_at(self, x: int, y: int) -> None:
        if not self.ensure_started():
            return

        def _show():
            from System.Drawing import Point
            import System.Windows.Forms as WinForms

            form = self._form
            if form is None:
                return

            area = WinForms.Screen.FromPoint(Point(x, y)).WorkingArea
            left = min(max(area.Left + 8, x - self.WIDTH + 22), area.Right - self.WIDTH - 8)
            top = min(max(area.Top + 8, y - self.HEIGHT - 14), area.Bottom - self.HEIGHT - 8)
            form.Location = Point(int(left), int(top))
            logger.debug(f"[Tray] show_native_panel x={left} y={top} cursor=({x},{y})")
            form.Show()
            form.Activate()

        self._invoke(_show)

    def hide(self) -> None:
        def _hide():
            form = self._form
            if form is not None:
                form.Hide()

        self._invoke(_hide)

    def stop(self) -> None:
        self._stopping = True

        def _shutdown():
            form = self._form
            context = self._context
            if form is not None and not form.IsDisposed:
                form.Close()
            if context is not None:
                context.ExitThread()

        self._invoke(_shutdown)
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None


class AppTray:
    """System tray integration for the webview app."""

    def __init__(self, bridge: AppTrayBridge):
        self.bridge = bridge
        self.icon = None
        self.panel_host: NativeTrayPanelHost | None = None
        self._create_tray()

    def _fallback_image(self, width: int, height: int, color1: str, color2: str):
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (width, height), color1)  # type: ignore[arg-type]
        drawer = ImageDraw.Draw(image)
        drawer.rectangle((width // 2, 0, width, height // 2), fill=color2)
        drawer.rectangle((0, height // 2, width // 2, height), fill=color2)
        return image

    def _create_tray(self):
        import pystray
        from PIL import Image

        try:
            ico = Image.open(p_app_icon)
        except Exception:
            ico = self._fallback_image(64, 64, "black", "white")

        self.icon = pystray.Icon("Speech Translate", ico, "Speech Translate")
        self.icon.run_detached()
        self._install_pointer_actions()

    def _create_panel_host(self) -> NativeTrayPanelHost:
        return NativeTrayPanelHost(self)

    def _ensure_panel_host(self) -> NativeTrayPanelHost:
        if self.panel_host is None:
            self.panel_host = self._create_panel_host()
        return self.panel_host

    def _install_pointer_actions(self) -> None:
        if self.icon is None or not hasattr(self.icon, "_message_handlers"):
            return

        from pystray._util import win32

        original = self.icon._message_handlers.get(win32.WM_NOTIFY)
        left_double_click = getattr(win32, "WM_LBUTTONDBLCLK", 0x0203)

        def _patched_on_notify(wparam, lparam):
            if lparam == win32.WM_RBUTTONUP:
                self.open_panel()
                return None
            if lparam in (win32.WM_LBUTTONUP, left_double_click):
                self.show_app()
                return None
            if original is not None:
                return original(wparam, lparam)
            return None

        self.icon._message_handlers[win32.WM_NOTIFY] = _patched_on_notify

    @staticmethod
    def _cursor_position() -> tuple[int, int]:
        try:
            from ctypes import wintypes

            point = wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
            return int(point.x), int(point.y)
        except Exception:
            return 1200, 800

    def open_panel(self, *_args):
        x, y = self._cursor_position()
        logger.debug(f"[Tray] open_panel cursor=({x},{y})")
        self._ensure_panel_host().show_at(x, y)

    def show_app(self, *_args):
        self.hide_panel()
        window = self.bridge.get_window()
        if window is not None:
            try:
                if hasattr(window, "restore"):
                    window.restore()
            except Exception:
                pass
            try:
                window.show()
            except Exception:
                pass
            try:
                window.bring_to_front()
            except Exception:
                pass

    def hide_panel(self):
        if self.panel_host is not None:
            self.panel_host.hide()

    def stop(self):
        if self.panel_host is not None:
            try:
                self.panel_host.stop()
            except Exception:
                logger.exception("Failed to stop tray panel host")
            self.panel_host = None
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass

    def exit_app(self, *_args):
        self.bridge.quit_app()
