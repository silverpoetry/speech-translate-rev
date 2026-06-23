from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)


class WebSettingsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        app_js_path = Path(to_add) / "speech_translate" / "web" / "app.js"
        index_html_path = Path(to_add) / "speech_translate" / "web" / "index.html"
        cls.app_js = app_js_path.read_text(encoding="utf-8")
        cls.index_html = index_html_path.read_text(encoding="utf-8")

    def test_save_import_settings_persists_tsv_export(self) -> None:
        self.assertIn(
            "function readExportToSelection() {",
            self.app_js,
        )
        self.assertIn(
            "['tsv', els.exportTsv],",
            self.app_js,
        )
        self.assertIn(
            "function collectSharedFileSettings() {",
            self.app_js,
        )
        self.assertIn(
            "async function persistSharedFileSettings() {",
            self.app_js,
        )

    def test_import_auto_save_bucket_includes_tsv_toggle(self) -> None:
        self.assertIn(
            "fileShared: new Set([",
            self.app_js,
        )
        self.assertIn(
            "'export_txt', 'export_txt_toolbar',",
            self.app_js,
        )
        self.assertIn("'export_tsv', 'export_tsv_toolbar',", self.app_js)
        self.assertIn("'dir_export', 'dir_export_file',", self.app_js)

    def test_settings_save_pushes_detached_window_updates_for_both_modes(self) -> None:
        self.assertIn("await pushDetachedConfigUpdates();", self.app_js)
        self.assertIn("for (const mode of ['tc', 'tl']) {", self.app_js)

    def test_legacy_detached_autosave_bucket_removed(self) -> None:
        self.assertNotIn("detached: new Set([", self.app_js)
        self.assertNotIn("saveDetachedSettings(false)", self.app_js)

    def test_settings_panel_summary_renderer_exists(self) -> None:
        self.assertIn("function renderSettingsPanelSummaries(data)", self.app_js)
        self.assertIn("summary.querySelector('.settings-panel-meta')", self.app_js)
        self.assertIn("renderSettingsPanelSummaries(data);", self.app_js)

    def test_sidebar_switch_uses_hidden_class_for_workspace_and_settings(self) -> None:
        self.assertIn("els.workspaceHub.classList.toggle('is-hidden', showSettings);", self.app_js)
        self.assertIn("els.settingsShell.classList.toggle('is-hidden', !showSettings);", self.app_js)
        self.assertNotIn("els.workspaceHub.style.display = showSettings ? 'none' : 'grid';", self.app_js)

    def test_app_level_actions_are_exposed_in_web_ui(self) -> None:
        self.assertIn("data-action=\"save-window-geometry\"", self.app_js)
        self.assertIn("data-action=\"quit-app\"", self.index_html)
        self.assertIn("await apiCall('save_main_window_geometry', true);", self.app_js)
        self.assertIn("await apiCall('quit_app');", self.app_js)

    def test_audio_device_refresh_action_exists(self) -> None:
        self.assertIn("action === 'refresh-audio-devices'", self.app_js)
        self.assertIn("await refreshAudioSourceOptions(els.hostAPI ? els.hostAPI.value : '', true);", self.app_js)
        self.assertIn("async function persistRecordDeviceSelection(hostApiValue, micValue, speakerValue)", self.app_js)

    def test_task_runtime_pills_are_rendered(self) -> None:
        self.assertIn("function renderTaskRuntimePills(data)", self.app_js)
        self.assertIn("renderTaskRuntimePills(data);", self.app_js)
        self.assertIn("renderTaskRuntimePills(state.data || {});", self.app_js)

    def test_model_workbench_summary_renderers_exist(self) -> None:
        self.assertIn("function summarizeModelDevicePreference(value)", self.app_js)
        self.assertIn("state.modelManagerState = modelUi;", self.app_js)
        self.assertIn("els.modelSelectionRuntime = $('model-selection-runtime');", self.app_js)
        self.assertIn("els.modelSelectionRuntimeMeta = $('model-selection-runtime-meta');", self.app_js)

    def test_file_workbench_summary_renderers_exist(self) -> None:
        self.assertIn("function summarizeFileSliceRange(start, end)", self.app_js)
        self.assertIn("function summarizeFilterDictionaryPath(pathValue)", self.app_js)
        self.assertIn("els.fileImportExportFormat = $('file-import-export-format');", self.app_js)
        self.assertIn("els.fileImportSliceRange = $('file-import-slice-range');", self.app_js)
        self.assertIn("els.fileImportFilterState = $('file-import-filter-state');", self.app_js)

    def test_file_workbench_exposes_jump_actions(self) -> None:
        self.assertIn('data-settings-jump="导出与切分"', self.index_html)
        self.assertIn('data-settings-jump="过滤与词典"', self.index_html)

    def test_model_workbench_exposes_network_jump_action(self) -> None:
        self.assertIn('data-settings-jump="网络与翻译"', self.index_html)

    def test_settings_toolbar_workbench_fields_are_bound(self) -> None:
        self.assertIn("els.httpProxyEnableToolbar = $('http_proxy_enable_toolbar');", self.app_js)
        self.assertIn("els.exportFormatToolbar = $('export_format_toolbar');", self.app_js)
        self.assertIn("els.exportTxtToolbar = $('export_txt_toolbar');", self.app_js)
        self.assertIn("els.decodingPresetToolbar = $('decoding_preset_toolbar');", self.app_js)
        self.assertIn("els.bestOfToolbar = $('best_of_toolbar');", self.app_js)
        self.assertIn("els.suppressBlankToolbar = $('suppress_blank_toolbar');", self.app_js)
        self.assertIn("function bindToolbarMirrorValues(pairs = [])", self.app_js)
        self.assertIn("function bindToolbarMirrorChecks(pairs = [])", self.app_js)
        self.assertIn("[els.hostAPIToolbar, els.hostAPI]", self.app_js)
        self.assertIn("[els.exportTxtToolbar, els.exportTxt]", self.app_js)
        self.assertIn("[els.decodingPresetToolbar, els.decodingPreset]", self.app_js)
        self.assertIn("[els.bestOfToolbar, els.bestOf]", self.app_js)
        self.assertIn("[els.suppressBlankToolbar, els.suppressBlank]", self.app_js)
        self.assertIn("function syncToolbarMirrorValues(pairs = [])", self.app_js)
        self.assertIn("function syncToolbarMirrorChecks(pairs = [])", self.app_js)
        self.assertIn("[els.exportFormat, els.exportFormatToolbar, '%Y-%m-%d %f {file}/{task-lang}']", self.app_js)
        self.assertIn("[els.exportTxt, els.exportTxtToolbar, true]", self.app_js)
        self.assertIn("[els.bestOf, els.bestOfToolbar, '3']", self.app_js)
        self.assertIn("[els.suppressBlank, els.suppressBlankToolbar, true]", self.app_js)
        self.assertIn("[els.autoOpenDirExport, els.autoOpenDirExportToolbar, true]", self.app_js)
        self.assertIn("[els.exportFormatToolbar, els.exportFormat, '%Y-%m-%d %f {file}/{task-lang}']", self.app_js)
        self.assertIn("[els.exportTxtToolbar, els.exportTxt, true]", self.app_js)

    def test_select_setting_persistence_uses_shared_binding_helper(self) -> None:
        self.assertIn("function bindSelectSettingPersistence(node, apiName, key, options = {})", self.app_js)
        self.assertIn("bindSelectSettingPersistence(els.mic, 'set_record_setting', 'mic');", self.app_js)
        self.assertIn("bindSelectSettingPersistence(els.speaker, 'set_record_setting', 'speaker');", self.app_js)
        self.assertIn("bindSelectSettingPersistence(els.modelImport, 'set_import_setting', 'model_f_import', {", self.app_js)

    def test_settings_toolbar_actions_exist(self) -> None:
        self.assertIn('data-action="save-settings"', self.index_html)
        self.assertIn('data-action="save-import-settings"', self.index_html)
        self.assertIn('data-action="refresh-audio-devices"', self.index_html)

    def test_show_main_window_and_open_current_log_actions_exist(self) -> None:
        self.assertIn("data-action=\"show-main-window\"", self.app_js)
        self.assertIn("await apiCall('show_main_window');", self.app_js)
        self.assertIn("data-action=\"open-current-log\"", self.app_js)
        self.assertIn("await apiCall('open_link', `file:///${logDir.replace(/\\\\\\\\/g, '/')", self.app_js)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
