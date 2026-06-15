from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

import speech_translate.settings_runtime as settings_runtime_module
from speech_translate.settings_runtime import create_settings_store, get_settings_store, sj


class SettingsRuntimeTests(unittest.TestCase):
    def test_settings_store_factory_creates_distinct_instances(self) -> None:
        self.assertIsNot(create_settings_store(), create_settings_store())

    def test_settings_store_accessor_returns_cached_singleton(self) -> None:
        self.assertIs(get_settings_store(), get_settings_store())
        self.assertIs(sj, get_settings_store())

    def test_settings_module_exports_runtime_api_and_singleton_name(self) -> None:
        self.assertEqual(settings_runtime_module.__all__, ["create_settings_store", "get_settings_store", "sj"])


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
