from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.linker import BridgeClass


class LinkerStructureTests(unittest.TestCase):
    def test_bridge_class_exposes_legacy_properties_through_runtime_objects(self) -> None:
        bridge = BridgeClass()

        bridge.recording = True
        bridge.current_rec_status = "busy"
        bridge.auto_detected_lang = "en"
        bridge.file_processing = True
        bridge.cancel_dl = True
        bridge.fg_color = "#abcdef"
        bridge.tc_sentences = ["hello"]
        bridge.tl_sentences = ["world"]

        self.assertTrue(bridge.recording_runtime.recording)
        self.assertEqual(bridge.recording_runtime.current_rec_status, "busy")
        self.assertEqual(bridge.live_text.auto_detected_lang, "en")
        self.assertTrue(bridge.file_runtime.file_processing)
        self.assertTrue(bridge.download.cancel_dl)
        self.assertEqual(bridge.visual.fg_color, "#abcdef")
        self.assertEqual(bridge.live_text.tc_sentences, ["hello"])
        self.assertEqual(bridge.live_text.tl_sentences, ["world"])

    def test_bridge_class_methods_update_legacy_and_runtime_state_consistently(self) -> None:
        bridge = BridgeClass()

        bridge.enable_rec()
        bridge.enable_file_process()
        bridge.enable_file_tc()
        bridge.enable_file_tl()
        bridge.disable_rec()
        bridge.disable_file_process()
        bridge.disable_file_tc()
        bridge.disable_file_tl()

        self.assertFalse(bridge.recording)
        self.assertFalse(bridge.file_processing)
        self.assertFalse(bridge.transcribing_file)
        self.assertFalse(bridge.translating_file)
        self.assertFalse(bridge.recording_runtime.recording)
        self.assertFalse(bridge.file_runtime.file_processing)
        self.assertFalse(bridge.file_runtime.transcribing_file)
        self.assertFalse(bridge.file_runtime.translating_file)

    def test_bridge_class_clear_all_resets_live_text_runtime_lists(self) -> None:
        bridge = BridgeClass()
        bridge.tc_sentences = ["a"]
        bridge.tl_sentences = ["b"]

        bridge.clear_all()

        self.assertEqual(bridge.live_text.tc_sentences, [])
        self.assertEqual(bridge.live_text.tl_sentences, [])
        self.assertEqual(bridge.tc_sentences, [])
        self.assertEqual(bridge.tl_sentences, [])


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
