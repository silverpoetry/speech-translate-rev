import os
import re
import sys
import unittest
from pathlib import Path

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)


class WebUiPreviewSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        web_dir = Path(to_add) / "speech_translate" / "web"
        cls.index_html = (web_dir / "index.html").read_text(encoding="utf-8")
        cls.preview_html = (web_dir / "ui-preview.html").read_text(encoding="utf-8")

    def test_preview_contains_model_manager_summary_nodes(self) -> None:
        for node_id in (
            "model-manager-dir-pill",
            "model-manager-selection-pill",
            "model-manager-cache-pill",
            "model-manager-download-pill",
        ):
            with self.subTest(node_id=node_id):
                self.assertIn(f'id="{node_id}"', self.preview_html)

    def test_preview_contains_global_statusbar_group_structure(self) -> None:
        self.assertIn('class="statusbar-group statusbar-left"', self.preview_html)
        self.assertIn('class="statusbar-group statusbar-right"', self.preview_html)

    def test_index_and_preview_share_same_core_feature_ids(self) -> None:
        required_ids = {
            "global-statusbar",
            "global-model-state",
            "global-task-state",
            "global-task-progress-wrap",
            "btn-load-model",
            "model_f_import",
            "record_visualizer_card",
            "model-manager-dir-pill",
            "model-manager-selection-pill",
            "model-manager-cache-pill",
            "model-manager-download-pill",
        }
        index_ids = set(re.findall(r'id="([^"]+)"', self.index_html))
        preview_ids = set(re.findall(r'id="([^"]+)"', self.preview_html))
        self.assertTrue(required_ids.issubset(index_ids))
        self.assertTrue(required_ids.issubset(preview_ids))

    def test_index_and_preview_share_model_selection_quick_actions(self) -> None:
        required_actions = {
            'data-action="load-model"',
            'data-action="check-model-current"',
            'data-action="check-all-models"',
            'data-settings-jump="Whisper 解码"',
            'data-settings-jump="网络与翻译"',
            'data-settings-jump="录制设置"',
            'data-settings-jump="系统与路径"',
        }
        for action in required_actions:
            with self.subTest(action=action):
                self.assertIn(action, self.index_html)
                self.assertIn(action, self.preview_html)

    def test_preview_contains_model_selection_summary_nodes(self) -> None:
        for node_id in (
            "model-selection-runtime",
            "model-selection-runtime-meta",
        ):
            with self.subTest(node_id=node_id):
                self.assertIn(f'id="{node_id}"', self.preview_html)

    def test_index_and_preview_share_file_workbench_quick_actions(self) -> None:
        required_actions = {
            'data-action="add-files-to-queue"',
            'data-action="pick-export-dir"',
            'data-action="save-import-settings"',
            'data-settings-jump="导出与切分"',
            'data-settings-jump="过滤与词典"',
        }
        for action in required_actions:
            with self.subTest(action=action):
                self.assertIn(action, self.index_html)
                self.assertIn(action, self.preview_html)

    def test_preview_contains_file_workbench_summary_nodes(self) -> None:
        for node_id in (
            "file-import-queue-inline-count",
            "file-import-format-state",
            "file-import-export-format",
            "file-import-export-format-meta",
            "file-import-filter-state",
            "file-import-filter-meta",
        ):
            with self.subTest(node_id=node_id):
                self.assertIn(f'id="{node_id}"', self.preview_html)

    def test_preview_contains_settings_workbench_nodes(self) -> None:
        for node_id in (
            "http_proxy_enable_toolbar",
            "https_proxy_enable_toolbar",
            "http_proxy_toolbar",
            "https_proxy_toolbar",
            "libre_link_toolbar",
            "export_format_toolbar",
            "auto_open_dir_export_toolbar",
            "segment_max_words_toolbar",
            "hostAPI_toolbar",
            "transcribe_rate_toolbar",
            "export_txt_toolbar",
            "export_srt_toolbar",
            "export_vtt_toolbar",
            "export_json_toolbar",
            "export_ass_toolbar",
            "export_mp4_toolbar",
            "rec_ask_confirmation_first_toolbar",
            "supress_hidden_to_tray_toolbar",
            "decoding_preset_toolbar",
            "temperature_toolbar",
            "use_en_model_toolbar",
            "fp16_toolbar",
            "best_of_toolbar",
            "beam_size_toolbar",
            "no_speech_threshold_toolbar",
            "logprob_threshold_toolbar",
            "suppress_blank_toolbar",
        ):
            with self.subTest(node_id=node_id):
                self.assertIn(f'id="{node_id}"', self.preview_html)

    def test_index_and_preview_default_to_realtime_panel(self) -> None:
        self.assertIn('class="menu-item is-active" data-nav-target="realtime"', self.index_html)
        self.assertIn('class="menu-item is-active" data-nav-target="realtime"', self.preview_html)
        self.assertIn('class="workflow-card tab-panel is-active" id="tab-realtime"', self.index_html)
        self.assertIn('class="workflow-card tab-panel is-active" id="tab-realtime"', self.preview_html)

    def test_only_one_default_active_panel_per_page(self) -> None:
        active_panel_pattern = r'class="workflow-card tab-panel is-active"'
        self.assertEqual(len(re.findall(active_panel_pattern, self.index_html)), 1)
        self.assertEqual(len(re.findall(active_panel_pattern, self.preview_html)), 1)

    def test_index_and_preview_expose_dedicated_detached_window_setting_ids(self) -> None:
        detached_ids = {
            "ex_tc_geometry",
            "ex_tc_always_on_top",
            "ex_tc_no_title_bar",
            "ex_tc_click_through",
            "ex_tc_opacity",
            "tb_ex_tc_font",
            "tb_ex_tc_font_bold",
            "tb_ex_tc_font_size",
            "tb_ex_tc_font_color",
            "tb_ex_tc_bg_color",
            "tb_ex_tc_limit_max",
            "tb_ex_tc_limit_max_per_line",
            "tb_ex_tc_max",
            "tb_ex_tc_max_per_line",
            "tb_ex_tc_use_conf_color",
            "ex_tl_geometry",
            "ex_tl_always_on_top",
            "ex_tl_no_title_bar",
            "ex_tl_click_through",
            "ex_tl_opacity",
            "tb_ex_tl_font",
            "tb_ex_tl_font_bold",
            "tb_ex_tl_font_size",
            "tb_ex_tl_font_color",
            "tb_ex_tl_bg_color",
            "tb_ex_tl_limit_max",
            "tb_ex_tl_limit_max_per_line",
            "tb_ex_tl_max",
            "tb_ex_tl_max_per_line",
            "tb_ex_tl_use_conf_color",
        }
        index_ids = set(re.findall(r'id="([^"]+)"', self.index_html))
        preview_ids = set(re.findall(r'id="([^"]+)"', self.preview_html))
        self.assertTrue(detached_ids.issubset(index_ids))
        self.assertTrue(detached_ids.issubset(preview_ids))

    def test_index_and_preview_remove_legacy_detached_editor_ids(self) -> None:
        legacy_ids = {
            "detached_mode_titlebar",
            "detached_mode_tc_btn",
            "detached_mode_tl_btn",
            "detached_font",
            "detached_font_size",
            "detached_font_color",
            "detached_geometry",
            "detached_bg_color",
            "detached_opacity",
            "detached_max",
            "detached_max_per_line",
            "detached_always_on_top",
            "detached_no_title_bar",
            "detached_click_through",
            "detached_font_bold",
            "detached_limit_max",
            "detached_limit_max_per_line",
            "detached_use_conf_color",
        }
        index_ids = set(re.findall(r'id="([^"]+)"', self.index_html))
        preview_ids = set(re.findall(r'id="([^"]+)"', self.preview_html))
        self.assertTrue(legacy_ids.isdisjoint(index_ids))
        self.assertTrue(legacy_ids.isdisjoint(preview_ids))


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
