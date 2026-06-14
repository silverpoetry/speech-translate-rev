from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import patch

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate import runtime_bootstrap


class RuntimeBootstrapTests(unittest.TestCase):
    def test_prepare_subprocess_kwargs_adds_hidden_startupinfo_on_windows(self) -> None:
        prepared = runtime_bootstrap.prepare_subprocess_kwargs({}, platform_name="Windows")
        self.assertIn("startupinfo", prepared)
        self.assertTrue(prepared["startupinfo"].dwFlags & runtime_bootstrap.subprocess.STARTF_USESHOWWINDOW)

    def test_prepare_subprocess_kwargs_preserves_existing_startupinfo(self) -> None:
        existing = runtime_bootstrap.subprocess.STARTUPINFO()
        prepared = runtime_bootstrap.prepare_subprocess_kwargs({"startupinfo": existing}, platform_name="Windows")
        self.assertIs(prepared["startupinfo"], existing)

    def test_install_no_console_popen_is_idempotent(self) -> None:
        original = runtime_bootstrap.subprocess.Popen
        try:
            runtime_bootstrap.install_no_console_popen()
            self.assertIs(runtime_bootstrap.subprocess.Popen, runtime_bootstrap.NoConsolePopen)
            runtime_bootstrap.install_no_console_popen()
            self.assertIs(runtime_bootstrap.subprocess.Popen, runtime_bootstrap.NoConsolePopen)
        finally:
            runtime_bootstrap.subprocess.Popen = original

    def test_get_whisper_load_api_caches_loaded_module(self) -> None:
        sentinel = object()
        fake_package = types.SimpleNamespace(load=sentinel)
        original = runtime_bootstrap._whisper_load_api
        runtime_bootstrap._whisper_load_api = None
        try:
            with patch.dict(sys.modules, {"speech_translate.utils.whisper": fake_package}):
                first = runtime_bootstrap.get_whisper_load_api()
                second = runtime_bootstrap.get_whisper_load_api()
        finally:
            runtime_bootstrap._whisper_load_api = original

        self.assertIs(first, sentinel)
        self.assertIs(second, sentinel)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
