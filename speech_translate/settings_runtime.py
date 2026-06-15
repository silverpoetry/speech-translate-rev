from __future__ import annotations

from ._path import dir_debug, dir_export, dir_log, dir_temp, dir_user, p_app_icon, p_app_settings
from .utils.setting import SettingJson


sj: SettingJson = SettingJson(
    p_app_settings,
    [dir_user, dir_temp, dir_log, dir_export, dir_debug],
    p_app_icon,
)


__all__ = [
    "sj",
]
