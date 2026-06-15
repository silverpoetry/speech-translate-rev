from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.webview_app import WebBridge, WebBridgeDependencies


class FakeController:
    def __init__(self) -> None:
        self.first_state_logged = False
        self.calls = []
        self.bound_window = None

    def bind_window(self, window) -> None:
        self.bound_window = window
        self.calls.append(("bind_window", window))

    def log_startup_marker(self, marker: str) -> None:
        self.calls.append(("log_startup_marker", marker))

    def handle_task_message(self, message: str, source: str = "general") -> None:
        self.calls.append(("handle_task_message", message, source))


class FakeStateViewBuilder(FakeController):
    def __init__(self) -> None:
        super().__init__()
        self.scan_started = 0

    def start_audio_source_scan(self) -> None:
        self.scan_started += 1

    def build_state(self):
        return {"ok": True}


class FakeDependenciesBuilder:
    def __init__(self) -> None:
        self.main_window_controller = FakeController()
        self.model_manager_controller = FakeController()
        self.import_queue_controller = FakeController()
        self.recording_controller = FakeController()
        self.state_view_builder = FakeStateViewBuilder()
        self.system_settings_controller = FakeController()
        self.detached_window_manager = object()
        self.detached_window_controller = FakeController()

    def __call__(self, bridge: WebBridge) -> WebBridgeDependencies:
        _ = bridge
        return WebBridgeDependencies(
            main_window_controller=self.main_window_controller,
            model_manager_controller=self.model_manager_controller,
            import_queue_controller=self.import_queue_controller,
            recording_controller=self.recording_controller,
            state_view_builder=self.state_view_builder,
            system_settings_controller=self.system_settings_controller,
            detached_window_manager=self.detached_window_manager,
            detached_window_controller=self.detached_window_controller,
        )


class WebviewAppTests(unittest.TestCase):
    def test_web_bridge_uses_injected_dependencies_and_bootstrapper(self) -> None:
        bootstrap_calls = []
        deps_builder = FakeDependenciesBuilder()

        bridge = WebBridge(
            dependencies_builder=deps_builder,
            bootstrapper=lambda: bootstrap_calls.append("boot"),
        )

        self.assertEqual(bootstrap_calls, ["boot"])
        self.assertIs(bridge.main_window_controller, deps_builder.main_window_controller)
        self.assertEqual(deps_builder.state_view_builder.scan_started, 1)

    def test_get_state_logs_first_marker_once(self) -> None:
        deps_builder = FakeDependenciesBuilder()
        bridge = WebBridge(dependencies_builder=deps_builder, bootstrapper=None)

        self.assertEqual(bridge.get_state(), {"ok": True})
        self.assertEqual(bridge.get_state(), {"ok": True})
        self.assertEqual(
            deps_builder.main_window_controller.calls,
            [("log_startup_marker", "first_get_state")],
        )

    def test_update_task_message_notifies_model_manager(self) -> None:
        deps_builder = FakeDependenciesBuilder()
        bridge = WebBridge(dependencies_builder=deps_builder, bootstrapper=None)

        bridge.update_task_message("loading", source="model-load")

        self.assertIn(
            ("handle_task_message", "loading", "model-load"),
            deps_builder.model_manager_controller.calls,
        )


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
