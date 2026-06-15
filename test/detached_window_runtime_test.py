from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.detached_window_runtime import DetachedWindowDeliveryRuntime


class DetachedWindowRuntimeTests(unittest.TestCase):
    def test_try_start_content_sender_is_single_flight(self) -> None:
        runtime = DetachedWindowDeliveryRuntime()
        self.assertTrue(runtime.try_start_content_sender("tc"))
        self.assertFalse(runtime.try_start_content_sender("tc"))
        runtime.stop_content_sender("tc")
        self.assertTrue(runtime.try_start_content_sender("tc"))

    def test_should_skip_duplicate_content_requires_same_pending_and_not_busy(self) -> None:
        runtime = DetachedWindowDeliveryRuntime()
        runtime.note_content_sent("tc", "hello")
        self.assertTrue(runtime.should_skip_duplicate_content("tc", "hello", "hello"))
        runtime.content_sender_busy["tc"] = True
        self.assertFalse(runtime.should_skip_duplicate_content("tc", "hello", "hello"))

    def test_drop_window_ref_preserves_pending_but_clears_delivery_state(self) -> None:
        runtime = DetachedWindowDeliveryRuntime()
        runtime.set_pending_content("tc", "hello")
        runtime.set_pending_config("tc", {"font": "Arial"})
        runtime.mark_window_loaded("tc", True)
        runtime.mark_window_content_ready("tc", True)
        runtime.note_content_sent("tc", "hello")
        runtime.note_config_sent("tc", "{}")
        runtime.content_sender_busy["tc"] = True

        runtime.drop_window_ref("tc")

        self.assertEqual(runtime.get_pending_content("tc"), "hello")
        self.assertEqual(runtime.get_pending_config("tc"), {"font": "Arial"})
        self.assertFalse(runtime.is_window_loaded("tc"))
        self.assertFalse(runtime.is_window_content_ready("tc"))
        self.assertIsNone(runtime.get_last_content_payload("tc"))
