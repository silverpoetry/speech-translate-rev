from __future__ import annotations

from ._path import dir_debug, dir_export, dir_log, dir_temp, dir_user, p_app_icon, p_app_settings
from .utils.setting import SettingJson


def create_settings_store() -> SettingJson:
    return SettingJson(
        p_app_settings,
        [dir_user, dir_temp, dir_log, dir_export, dir_debug],
        p_app_icon,
    )


_settings_singleton: SettingJson | None = None


def get_settings_store() -> SettingJson:
    global _settings_singleton
    if _settings_singleton is None:
        _settings_singleton = create_settings_store()
    return _settings_singleton


__all__ = [
    "create_settings_store",
    "get_settings_store",
]
