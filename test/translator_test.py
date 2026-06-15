from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.utils.translate import translator as translator_module


class FakeManager:
    def __init__(self) -> None:
        self.shutdown_calls = 0

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class TranslatorFacadeTests(unittest.TestCase):
    def test_shutdown_selenium_translator_delegates_to_manager(self) -> None:
        previous_manager = translator_module._selenium_translator_manager
        fake_manager = FakeManager()
        try:
            translator_module._selenium_translator_manager = fake_manager
            translator_module.shutdown_selenium_translator()
        finally:
            translator_module._selenium_translator_manager = previous_manager

        self.assertEqual(fake_manager.shutdown_calls, 1)

    def test_resolve_language_pair_falls_back_to_auto_for_source(self) -> None:
        source_code, target_code = translator_module._resolve_language_pair(
            {"english": "en", "chinese": "zh"},
            "eng",
            "chinese",
            engine_label="Test",
        )

        self.assertEqual(source_code, "en")
        self.assertEqual(target_code, "zh")

    def test_google_translate_returns_dependency_error_when_deep_translator_missing(self) -> None:
        previous_loader = translator_module._ensure_deep_translator_connection
        try:
            translator_module._ensure_deep_translator_connection = lambda: (None, None)
            success, result = translator_module.google_tl(["hello"], "english", "chinese", {})
        finally:
            translator_module._ensure_deep_translator_connection = previous_loader

        self.assertFalse(success)
        self.assertEqual(result, "Error: deep_translator is unavailable")

    def test_selenium_manager_settings_provider_is_lazy_callable(self) -> None:
        previous_provider = translator_module._selenium_translator_manager._settings_snapshot_provider
        try:
            translator_module._selenium_translator_manager._settings_snapshot_provider = lambda: {"selenium_compact_level": 1}
            self.assertEqual(
                translator_module._selenium_translator_manager._settings_snapshot_provider(),
                {"selenium_compact_level": 1},
            )
        finally:
            translator_module._selenium_translator_manager._settings_snapshot_provider = previous_provider


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
