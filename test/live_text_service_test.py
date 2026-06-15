from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.live_text_service import LiveTextRenderer


class FakeSettings:
    def __init__(self) -> None:
        self.cache = {
            "separate_with": "\\n",
            "gradient_low_conf": "#FF0000",
            "gradient_high_conf": "#00FF00",
            "colorize_per_segment": False,
            "colorize_per_word": False,
            "tb_mw_tc_limit_max": False,
            "tb_mw_tc_max": 300,
            "tb_mw_tc_limit_max_per_line": False,
            "tb_mw_tc_max_per_line": 30,
            "tb_mw_tc_font": "Arial",
            "tb_mw_tc_font_bold": False,
            "tb_mw_tc_font_size": 12,
            "tb_mw_tc_use_conf_color": False,
            "tb_mw_tc_font_color": "#111111",
            "tb_ex_tc_limit_max": False,
            "tb_ex_tc_max": 300,
            "tb_ex_tc_limit_max_per_line": False,
            "tb_ex_tc_max_per_line": 30,
            "tb_ex_tc_font": "Arial",
            "tb_ex_tc_font_bold": True,
            "tb_ex_tc_font_size": 12,
            "tb_ex_tc_use_conf_color": False,
            "tb_ex_tc_font_color": "#222222",
            "tb_mw_tl_limit_max": False,
            "tb_mw_tl_max": 300,
            "tb_mw_tl_limit_max_per_line": False,
            "tb_mw_tl_max_per_line": 30,
            "tb_mw_tl_font": "Arial",
            "tb_mw_tl_font_bold": False,
            "tb_mw_tl_font_size": 12,
            "tb_mw_tl_use_conf_color": False,
            "tb_mw_tl_font_color": "#333333",
            "tb_ex_tl_limit_max": False,
            "tb_ex_tl_max": 300,
            "tb_ex_tl_limit_max_per_line": False,
            "tb_ex_tl_max_per_line": 30,
            "tb_ex_tl_font": "Arial",
            "tb_ex_tl_font_bold": False,
            "tb_ex_tl_font_size": 12,
            "tb_ex_tl_use_conf_color": False,
            "tb_ex_tl_font_color": "#444444",
        }


class FakeBridge:
    def __init__(self) -> None:
        self.append_calls = []
        self.html_updates = []
        self.clear_calls = []

    def append_live_text(self, target: str, text: str, separator: str = "") -> None:
        self.append_calls.append((target, text, separator))

    def update_live_html(self, target: str, html: str) -> None:
        self.html_updates.append((target, html))

    def clear_live(self, prefix: str = "") -> None:
        self.clear_calls.append(prefix)


class FakeResult:
    def __init__(self, text: str) -> None:
        self.text = text

    def __len__(self) -> int:
        return len(self.text)


class FakeWord:
    def __init__(self, word: str, probability: float, word_id: int) -> None:
        self.word = word
        self.probability = probability
        self.id = word_id


class FakeSegment:
    def __init__(self, text: str, segment_id: int, probabilities: list[float]) -> None:
        self.text = text
        self.id = segment_id
        self.words = [FakeWord(word=text.strip(), probability=value, word_id=index) for index, value in enumerate(probabilities)]


class FakeSegmentedResult(FakeResult):
    def __init__(self, segments) -> None:
        self.segments = list(segments)
        super().__init__(" ".join(segment.text for segment in segments).strip())


class LiveTextRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = FakeSettings()
        self.renderer = LiveTextRenderer(self.settings)
        self.bridge = FakeBridge()

    def test_append_incremental_text_updates_main_and_detached_targets(self) -> None:
        self.renderer.append_incremental_text(self.bridge, text="hello", mode="tc", separator="<br />")

        self.assertEqual(
            self.bridge.append_calls,
            [
                ("main_transcribed", "hello", "<br />"),
                ("detached_transcribed", "hello", "<br />"),
            ],
        )

    def test_update_stream_renders_main_and_detached_html(self) -> None:
        self.renderer.update_stream(
            self.bridge,
            mode="tc",
            sentences=[FakeResult("hello")],
            new_result=FakeResult("world"),
            separator="<br />",
            fg_color="#000000",
        )

        targets = [target for target, _html in self.bridge.html_updates]
        self.assertEqual(targets, ["main_transcribed_html", "detached_transcribed_html"])
        self.assertIn("hello<br />", self.bridge.html_updates[0][1])
        self.assertIn("world<br />", self.bridge.html_updates[0][1])

    def test_map_result_lists_colorizes_segments_when_enabled(self) -> None:
        self.settings.cache["colorize_per_segment"] = True
        store_list = []
        result = FakeSegmentedResult([FakeSegment(" alpha", 0, [0.5]), FakeSegment("beta", 1, [1.0])])

        total_len = self.renderer.map_result_lists([result], store_list, "<br />")

        self.assertEqual(total_len, len("alpha") + len("beta"))
        self.assertEqual(store_list[-1]["text"], "beta<br />")
        self.assertIsNotNone(store_list[0]["color"])

    def test_clear_target_delegates_to_bridge(self) -> None:
        self.renderer.clear_target(self.bridge, "main_transcribed")
        self.assertEqual(self.bridge.clear_calls, ["main_transcribed"])


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
