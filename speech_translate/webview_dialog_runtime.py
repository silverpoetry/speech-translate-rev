from __future__ import annotations

from importlib import import_module
from typing import Literal


def load_webview_module():
    return import_module("webview")


def resolve_file_dialog(webview_module, dialog_kind: Literal["open", "folder"]):
    file_dialog = getattr(webview_module, "FileDialog", object)
    if dialog_kind == "open":
        modern = getattr(file_dialog, "OPEN", None)
        return modern if modern is not None else getattr(webview_module, "OPEN_DIALOG")
    if dialog_kind == "folder":
        modern = getattr(file_dialog, "FOLDER", None)
        return modern if modern is not None else getattr(webview_module, "FOLDER_DIALOG")
    raise ValueError(f"Unsupported dialog kind: {dialog_kind}")


def create_file_dialog(
    window,
    *,
    dialog_kind: Literal["open", "folder"],
    directory: str | None = None,
    allow_multiple: bool = False,
    file_types=None,
):
    webview_module = load_webview_module()
    dialog = resolve_file_dialog(webview_module, dialog_kind)
    kwargs = {}
    if directory is not None:
        kwargs["directory"] = directory
    if allow_multiple:
        kwargs["allow_multiple"] = True
    if file_types is not None:
        kwargs["file_types"] = file_types
    return window.create_file_dialog(dialog, **kwargs)
