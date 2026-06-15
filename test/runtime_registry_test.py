from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.runtime_registry import BridgeStateRegistry, SettingsRegistry


class FakeSettings:
    def __init__(self, value: str) -> None:
        self.cache = {"value": value}


class RuntimeRegistryTests(unittest.TestCase):
    def test_bridge_state_registry_override_restores_previous_state(self) -> None:
        registry = BridgeStateRegistry()
        original = object()
        override = object()
        registry.set(original)

        with registry.override(override):
            self.assertIs(registry.get(), override)

        self.assertIs(registry.get(), original)

    def test_bridge_state_registry_override_restores_after_exception(self) -> None:
        registry = BridgeStateRegistry()
        original = object()
        override = object()
        registry.set(original)

        with self.assertRaisesRegex(RuntimeError, "boom"):
            with registry.override(override):
                self.assertIs(registry.get(), override)
                raise RuntimeError("boom")

        self.assertIs(registry.get(), original)

    def test_settings_registry_override_restores_previous_settings(self) -> None:
        registry = SettingsRegistry()
        original = FakeSettings("original")
        override = FakeSettings("override")
        registry.set(original)

        with registry.override(override):
            self.assertEqual(registry.get().cache["value"], "override")

        self.assertEqual(registry.get().cache["value"], "original")


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
