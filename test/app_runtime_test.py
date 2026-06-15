from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.app_runtime import BridgeRuntimeRoot, bc


class AppRuntimeStructureTests(unittest.TestCase):
    def test_runtime_root_owns_runtime_state_without_legacy_bridge_methods(self) -> None:
        bridge = BridgeRuntimeRoot()

        self.assertTrue(hasattr(bridge, "visual"))
        self.assertTrue(hasattr(bridge, "file_runtime"))
        self.assertTrue(hasattr(bridge, "download"))
        self.assertTrue(hasattr(bridge, "recording_runtime"))
        self.assertTrue(hasattr(bridge, "live_text"))
        self.assertFalse(hasattr(bridge, "clear_all"))
        self.assertFalse(hasattr(bridge, "enable_rec"))

    def test_default_runtime_singleton_uses_runtime_root(self) -> None:
        self.assertIsInstance(bc, BridgeRuntimeRoot)
        self.assertFalse(hasattr(bc, "clear_all"))


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
