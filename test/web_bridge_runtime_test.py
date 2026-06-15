from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.web_bridge_runtime import WebBridgeRegistry


class DummyState:
    def __init__(self) -> None:
        self.web_bridge = None


class WebBridgeRuntimeTests(unittest.TestCase):
    def test_registry_reads_and_writes_bridge_from_state_object(self) -> None:
        state = DummyState()
        registry = WebBridgeRegistry(state=state)
        bridge = object()

        registry.set(bridge)

        self.assertIs(state.web_bridge, bridge)
        self.assertIs(registry.get(), bridge)

    def test_registry_can_resolve_bridge_state_lazily(self) -> None:
        state = DummyState()
        calls = []
        registry = WebBridgeRegistry(state_provider=lambda: calls.append("resolved") or state)

        self.assertIsNone(registry.get())
        registry.set("bridge")

        self.assertEqual(calls, ["resolved", "resolved"])
        self.assertEqual(state.web_bridge, "bridge")


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
