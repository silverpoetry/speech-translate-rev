from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.bridge_runtime_state import BridgeDownloadRuntime
from speech_translate.runtime_registry import bridge_state_registry
from speech_translate.utils.whisper.download import (
    DownloadBridgeAdapter,
    DownloadCancellationAdapter,
    TaskReporter,
    _build_bridge_task_reporter,
    _build_download_execution_hooks,
    whisper_download_headless,
)


class FakeUrlResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def info(self):
        return {"Content-Length": "4"}

    def read(self, _buffer_size):
        return b""


class ChunkedUrlResponse:
    def __init__(self, chunks, headers=None):
        self._chunks = list(chunks)
        self._headers = {} if headers is None else dict(headers)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def info(self):
        return self._headers

    def read(self, _buffer_size):
        return self._chunks.pop(0) if self._chunks else b""


class FakeDownloadBridge:
    def __init__(self) -> None:
        self.task_titles = []
        self.messages = []
        self.progress = []
        self.finished = []
        self.errors = []

    def reset_task_state(self, title: str) -> None:
        self.task_titles.append(title)

    def update_task_message(self, message: str, source: str = "general") -> None:
        self.messages.append((source, message))

    def update_task_progress(self, progress: float, source: str = "general") -> None:
        self.progress.append((source, progress))

    def finish_task(self, message: str = "") -> None:
        self.finished.append(message)

    def update_task_error(self, error: str) -> None:
        self.errors.append(error)


class FakeCancellationState:
    def __init__(self, *, cancel_dl: bool) -> None:
        self.cancel_dl = cancel_dl


class FakeBridgeRegistry:
    def __init__(self, bridge=None) -> None:
        self.bridge = bridge

    def get(self):
        return self.bridge

    def set(self, bridge) -> None:
        self.bridge = bridge


class WhisperDownloadTests(unittest.TestCase):
    def test_download_cancellation_adapter_default_provider_reads_download_runtime(self) -> None:
        from speech_translate.utils.whisper.download import DownloadCancellationAdapter

        fake_bridge = type("FakeBridgeState", (), {"download": BridgeDownloadRuntime(cancel_dl=True)})()
        with bridge_state_registry.override(fake_bridge):
            adapter = DownloadCancellationAdapter()
            self.assertTrue(adapter.cancel_requested())

    def test_build_bridge_task_reporter_supports_injected_bridge_adapter(self) -> None:
        bridge = FakeDownloadBridge()
        reporter = _build_bridge_task_reporter(bridge_adapter=DownloadBridgeAdapter(bridge=bridge))

        reporter.reset_task_state("Download")
        reporter.update_task_message("working")
        reporter.update_task_progress(25.0)
        reporter.finish_task("done")
        reporter.update_task_error("boom")

        self.assertEqual(bridge.task_titles, ["Download"])
        self.assertEqual(bridge.messages[-1][1], "working")
        self.assertEqual(bridge.progress[-1][1], 25.0)
        self.assertEqual(bridge.finished, ["done"])
        self.assertEqual(bridge.errors, ["boom"])

    def test_build_bridge_task_reporter_supports_registry_backed_adapter(self) -> None:
        bridge = FakeDownloadBridge()
        reporter = _build_bridge_task_reporter(
            bridge_adapter=DownloadBridgeAdapter(bridge_registry=FakeBridgeRegistry(bridge)),
        )

        reporter.reset_task_state("Download")

        self.assertEqual(bridge.task_titles, ["Download"])

    def test_build_download_execution_hooks_supports_injected_cancellation_adapter(self) -> None:
        cancellation_state = FakeCancellationState(cancel_dl=True)
        hooks = _build_download_execution_hooks(
            reporter=TaskReporter(),
            cancellation_adapter=DownloadCancellationAdapter(state=cancellation_state),
        )

        self.assertTrue(hooks.cancel_requested())
        hooks.clear_cancel_requested()
        self.assertFalse(cancellation_state.cancel_dl)

    def test_whisper_download_headless_uses_injected_cancel_hooks(self) -> None:
        from speech_translate.utils.whisper import download as download_module

        previous_urlopen = download_module.urllib.request.urlopen
        cancel_state = {"requested": True, "cleared": False}
        cancelled = []
        reporter_events = []
        try:
            download_module.urllib.request.urlopen = lambda _url: FakeUrlResponse()

            with tempfile.TemporaryDirectory() as temp_dir:
                result = whisper_download_headless(
                    "small",
                    "https://example.com/0123456789abcdef/model.pt",
                    temp_dir,
                    lambda: cancelled.append("cancelled"),
                    None,
                    None,
                    hooks=download_module.DownloadExecutionHooks(
                        reporter=TaskReporter(
                            reset_task_state=lambda title: reporter_events.append(("reset", title)),
                            finish_task=lambda message: reporter_events.append(("finish", message)),
                        ),
                        cancel_requested=lambda: cancel_state["requested"],
                        clear_cancel_requested=lambda: cancel_state.update(requested=False, cleared=True),
                        start_callback=lambda callback: callback() if callback is not None else None,
                    ),
                )
        finally:
            download_module.urllib.request.urlopen = previous_urlopen

        self.assertFalse(result)
        self.assertEqual(cancelled, ["cancelled"])
        self.assertTrue(cancel_state["cleared"])
        self.assertIn(("finish", "Download Cancelled"), reporter_events)

    def test_whisper_download_headless_runs_cancel_callback(self) -> None:
        from speech_translate.utils.whisper import download as download_module

        previous_urlopen = download_module.urllib.request.urlopen
        previous_callback_starter = download_module.start_optional_callback
        cancelled = []
        reporter_events = []
        fake_bridge = type("FakeBridgeState", (), {"download": BridgeDownloadRuntime(cancel_dl=True)})()
        try:
            download_module.urllib.request.urlopen = lambda _url: FakeUrlResponse()
            download_module.start_optional_callback = lambda callback: callback() if callback is not None else None
            with bridge_state_registry.override(fake_bridge):
                with tempfile.TemporaryDirectory() as temp_dir:
                    result = whisper_download_headless(
                        "small",
                        "https://example.com/0123456789abcdef/model.pt",
                        temp_dir,
                        lambda: cancelled.append("cancelled"),
                        None,
                        None,
                        reporter=TaskReporter(
                            reset_task_state=lambda title: reporter_events.append(("reset", title)),
                            finish_task=lambda message: reporter_events.append(("finish", message)),
                        ),
                    )
        finally:
            download_module.urllib.request.urlopen = previous_urlopen
            download_module.start_optional_callback = previous_callback_starter

        self.assertFalse(result)
        self.assertEqual(cancelled, ["cancelled"])
        self.assertIn(("finish", "Download Cancelled"), reporter_events)

    def test_whisper_download_headless_handles_missing_content_length(self) -> None:
        from speech_translate.utils.whisper import download as download_module

        payload = b"abcd"
        expected_sha = hashlib.sha256(payload).hexdigest()
        previous_urlopen = download_module.urllib.request.urlopen
        messages = []
        try:
            download_module.urllib.request.urlopen = lambda _url: ChunkedUrlResponse([payload], headers={})

            with tempfile.TemporaryDirectory() as temp_dir:
                result = whisper_download_headless(
                    "small",
                    f"https://example.com/{expected_sha}/model.pt",
                    temp_dir,
                    None,
                    None,
                    None,
                    reporter=TaskReporter(update_task_message=messages.append),
                )
        finally:
            download_module.urllib.request.urlopen = previous_urlopen

        self.assertTrue(result)
        self.assertTrue(any("Unknown" in message for message in messages))

    def test_download_model_whisper_backend_does_not_require_huggingface_runtime(self) -> None:
        from speech_translate.utils.whisper import download as download_module

        previous_whisper_url = download_module._resolve_whisper_model_url
        previous_downloader = download_module.whisper_download_headless
        previous_faster_repo = download_module._resolve_faster_whisper_repo_id
        captured = {}
        try:
            download_module._resolve_whisper_model_url = lambda model_key: f"https://example.com/{model_key}/model.pt"
            download_module._resolve_faster_whisper_repo_id = lambda _model_key: (_ for _ in ()).throw(
                AssertionError("faster-whisper path should not be used")
            )

            def fake_whisper_download_headless(model_name, url, download_root, cancel_func, after_func, failed_func, **kwargs):
                captured.update(
                    {
                        "model_name": model_name,
                        "url": url,
                        "download_root": download_root,
                        "kwargs": dict(kwargs),
                    }
                )
                return "ok"

            download_module.whisper_download_headless = fake_whisper_download_headless
            result = download_module.download_model(
                "small",
                use_faster_whisper=False,
                download_root="D:\\model-cache",
                reporter=TaskReporter(),
                progress_floor=5.0,
                progress_ceiling=90.0,
            )
        finally:
            download_module._resolve_whisper_model_url = previous_whisper_url
            download_module.whisper_download_headless = previous_downloader
            download_module._resolve_faster_whisper_repo_id = previous_faster_repo

        self.assertEqual(result, "ok")
        self.assertEqual(captured["model_name"], "small")
        self.assertEqual(captured["url"], "https://example.com/small/model.pt")
        self.assertEqual(captured["download_root"], "D:\\model-cache")
        self.assertEqual(captured["kwargs"]["progress_floor"], 5.0)
        self.assertEqual(captured["kwargs"]["progress_ceiling"], 90.0)

    def test_verify_model_faster_whisper_prefers_direct_model_dir_without_huggingface_runtime(self) -> None:
        from speech_translate.utils.whisper import download as download_module

        previous_repo_resolver = download_module._resolve_faster_whisper_repo_id
        previous_folder_name_getter = download_module.get_huggingface_repo_folder_name
        try:
            download_module._resolve_faster_whisper_repo_id = lambda _model_key: "Systran/faster-whisper-small"
            download_module.get_huggingface_repo_folder_name = lambda: (_ for _ in ()).throw(
                AssertionError("huggingface runtime should not be required for direct model dir verification")
            )

            with tempfile.TemporaryDirectory() as temp_dir:
                model_dir = os.path.join(temp_dir, "faster-whisper-small")
                os.makedirs(model_dir, exist_ok=True)
                with open(os.path.join(model_dir, "config.json"), "w", encoding="utf-8") as file:
                    file.write("{}")
                with open(os.path.join(model_dir, "model.bin"), "wb") as file:
                    file.write(b"weights")

                result = download_module.verify_model_faster_whisper("small", temp_dir)
        finally:
            download_module._resolve_faster_whisper_repo_id = previous_repo_resolver
            download_module.get_huggingface_repo_folder_name = previous_folder_name_getter

        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
