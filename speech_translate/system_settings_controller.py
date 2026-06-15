from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict

from speech_translate._path import dir_export, dir_log, dir_user
from speech_translate.controller_protocols import SettingsStore, SystemSettingsBridge
from speech_translate.log_helpers import logger
from speech_translate.webview_runtime import create_file_dialog
from speech_translate.utils.helper import open_folder, open_url
from speech_translate.utils.whisper.helper import model_select_dict


class SystemSettingsController:
    """Owns settings persistence, directory helpers, log access, and external-open actions."""

    def __init__(self, bridge: SystemSettingsBridge, settings: SettingsStore, path_config: Dict[str, str]):
        self.bridge = bridge
        self.settings = settings
        self.dir_debug = path_config["dir_debug"]
        self.dir_export = path_config["dir_export"]
        self.dir_log = path_config["dir_log"]
        self.dir_user = path_config["dir_user"]

    def _settings_value(self, key: str, default: object = None) -> object:
        return self.settings.cache.get(key, default)

    def _directory_mapping(self) -> Dict[str, str]:
        return {
            "export": self.resolve_export_dir(),
            "log": self.resolve_log_dir(),
            "debug": self.dir_debug,
            "model": self.bridge._resolve_model_dir(),
        }

    def _directory_selection_targets(self) -> Dict[str, tuple[str, str]]:
        return {
            "export": ("dir_export", self.resolve_export_dir()),
            "model": ("dir_model", self.bridge._resolve_model_dir()),
            "selenium_chrome": ("selenium_chrome_user_data_dir", self.resolve_selenium_chrome_user_data_dir()),
        }

    def _normalize_selenium_settings_payload(self, payload: object) -> Dict[str, object]:
        data = payload if isinstance(payload, dict) else {}
        compact = max(0, min(3, int(data.get("compact_level", 2))))
        z_order_raw = str(data.get("z_order_mode", "behind-main")).strip().lower()
        z_order = z_order_raw if z_order_raw in {"normal", "behind-main", "bottom"} else "behind-main"
        auto_close = bool(data.get("auto_close_on_task_done", True))
        chrome_user_data_dir = str(data.get("chrome_user_data_dir", "")).strip()
        return {
            "selenium_compact_level": compact,
            "selenium_z_order_mode": z_order,
            "selenium_auto_close_on_task_done": auto_close,
            "selenium_chrome_user_data_dir": chrome_user_data_dir,
        }

    def _normalize_setting_value(self, key: str, value: object) -> object:
        if key == "selenium_compact_level":
            return max(0, min(3, int(value)))
        if key == "selenium_z_order_mode":
            as_text = str(value).strip().lower()
            return as_text if as_text in {"normal", "behind-main", "bottom"} else "behind-main"
        if key == "selenium_auto_close_on_task_done":
            return bool(value)
        if key == "selenium_chrome_user_data_dir":
            return str(value or "").strip()
        return value

    def open_directory(self, name: str) -> Dict[str, str]:
        target = self._directory_mapping().get(name)
        if target:
            open_folder(target)
        return {"target": target or ""}

    def select_directory(self, name: str) -> Dict[str, object]:
        setting_info = self._directory_selection_targets().get(str(name or "").strip().lower())
        if not setting_info:
            return {"ok": False, "message": "Unsupported directory target", "path": ""}

        setting_key, default_dir = setting_info
        window = self.bridge.get_window()
        if not window:
            return {"ok": False, "message": "Window not ready", "path": ""}

        try:
            selected = create_file_dialog(window, dialog_kind="folder", directory=default_dir)
        except Exception as exc:
            logger.exception(exc)
            return {"ok": False, "message": str(exc), "path": ""}

        if not selected:
            return {"ok": False, "message": "No folder selected", "path": default_dir}
        selected_path = str(selected[0] if isinstance(selected, (list, tuple)) else selected).strip()
        if not selected_path:
            return {"ok": False, "message": "No folder selected", "path": default_dir}

        self.settings.save_key(setting_key, selected_path)
        if setting_key == "dir_model":
            self.bridge.model_manager_controller.clear_model_status_cache()
        return {"ok": True, "message": "Directory selected", "path": selected_path, "setting": setting_key}

    def open_link(self, url: str) -> Dict[str, str]:
        open_url(url)
        return {"url": url}

    def open_hallucination_filter(self, target: str) -> Dict[str, object]:
        try:
            from speech_translate._path import p_filter_file_import, p_filter_rec
            from speech_translate.utils.whisper.helper import create_hallucination_filter

            path = p_filter_rec if target == "rec" else p_filter_file_import
            if not os.path.exists(path):
                create_hallucination_filter("rec" if target == "rec" else "file")

            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                import subprocess

                subprocess.Popen(["open", path])
            else:
                import subprocess

                subprocess.Popen(["xdg-open", path])
            return {"ok": True}
        except Exception as exc:
            logger.exception(exc)
            return {"ok": False, "message": str(exc)}

    def notify(self, title: str, message: str) -> Dict[str, str]:
        logger.info(f"{title}: {message}")
        return {"title": title, "message": message}

    def resolve_export_dir(self) -> str:
        configured = self._settings_value("dir_export", "auto")
        return configured if configured != "auto" else self.dir_export

    def resolve_log_dir(self) -> str:
        configured = self._settings_value("dir_log", "auto")
        return configured if configured != "auto" else self.dir_log

    def resolve_selenium_chrome_user_data_dir(self) -> str:
        configured = str(self._settings_value("selenium_chrome_user_data_dir", "") or "").strip()
        return configured if configured else str(Path(self.dir_user) / "selenium_chrome_profile")

    def get_setting(self, key: str) -> object | None:
        return self._settings_value(key)

    def set_setting(self, key: str, value: object) -> Dict[str, object]:
        if key == "selenium_settings":
            normalized = self._normalize_selenium_settings_payload(value)
            for setting_key, setting_value in normalized.items():
                self.settings.save_key(setting_key, setting_value)

            return {
                "key": key,
                "value": {setting_key: self.settings.cache.get(setting_key, setting_value) for setting_key, setting_value in normalized.items()},
            }

        value = self._normalize_setting_value(key, value)
        self.settings.save_key(key, value)
        if key == "log_level":
            from speech_translate._logging import change_log_level

            change_log_level(str(value))
        return {"key": key, "value": self.settings.cache.get(key)}

    def set_import_setting(self, key: str, value: object) -> Dict[str, object]:
        if key in {"model_f_import", "model_mw"}:
            value = value if value in model_select_dict else value
        self.settings.save_key(key, value)
        return {"key": key, "value": self.settings.cache.get(key)}

    def set_record_setting(self, key: str, value: object) -> Dict[str, object]:
        if key == "model_device_preference":
            normalized = str(value or "auto").strip().lower()
            value = normalized if normalized in {"auto", "cpu", "cuda"} else "auto"
        self.settings.save_key(key, value)
        return {"key": key, "value": self.settings.cache.get(key)}

    def get_log_file_name(self) -> str:
        from speech_translate._logging import current_log

        return current_log

    def get_log_content(self) -> str:
        log_path = Path(self.dir_log) / self.get_log_file_name()
        try:
            content = log_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return f"Log file not found: {log_path}"
        except Exception as exc:
            logger.exception(exc)
            return f"Failed to read log file: {exc}"

        return content[-200000:] if len(content) > 200000 else content

    def refresh_log(self) -> Dict[str, str]:
        return {"content": self.get_log_content(), "file": self.get_log_file_name()}

    def clear_log(self) -> Dict[str, str]:
        from speech_translate._logging import clear_current_log_file

        clear_current_log_file()
        logger.info("Log cleared from web UI")
        return self.refresh_log()


DEFAULT_PATH_CONFIG = {
    "dir_debug": "",
    "dir_export": dir_export,
    "dir_log": dir_log,
    "dir_user": dir_user,
}
