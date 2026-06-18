from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.utils.translate.selenium_runtime import SeleniumTranslatorConfig
from speech_translate.utils.translate.selenium_web_translator import SeleniumWebTranslator


class SeleniumWebTranslatorTests(unittest.TestCase):
    def test_page_translate_timeout_returns_original_lines_when_text_stays_unchanged(self) -> None:
        translator = SeleniumWebTranslator(SeleniumTranslatorConfig())
        source_lines = ["alpha", "beta"]

        translator._ensure_page_template_loaded = lambda _lang=None: None  # type: ignore[method-assign]
        translator._set_payload_text = lambda _lines: "alpha\nbeta"  # type: ignore[method-assign]
        translator._wait_translation_event = lambda baseline, timeout: baseline  # type: ignore[method-assign]
        translator._read_payload_lines = lambda: ["alpha", "beta"]  # type: ignore[method-assign]
        translator._read_payload_text = lambda: "alpha\nbeta"  # type: ignore[method-assign]

        result = translator.translate_lines_via_page_translate(
            source_lines,
            source_lang="en",
            target_lang="zh-CN",
            wait_timeout_sec=0.01,
        )

        self.assertEqual(result, source_lines)

    def test_page_translate_timeout_exception_returns_original_lines(self) -> None:
        translator = SeleniumWebTranslator(SeleniumTranslatorConfig())
        source_lines = ["alpha"]

        translator._ensure_page_template_loaded = lambda _lang=None: None  # type: ignore[method-assign]
        translator._set_payload_text = lambda _lines: "alpha"  # type: ignore[method-assign]
        translator._wait_translation_event = lambda _baseline, _timeout: (_ for _ in ()).throw(RuntimeError("timeout"))  # type: ignore[method-assign]

        result = translator.translate_lines_via_page_translate(
            source_lines,
            source_lang="en",
            target_lang="zh-CN",
            wait_timeout_sec=0.01,
        )

        self.assertEqual(result, source_lines)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
