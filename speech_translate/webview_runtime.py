from __future__ import annotations

from importlib import import_module
from typing import Literal


def load_webview_runtime():
    return import_module("webview")


def resolve_file_dialog(webview_module, dialog_kind: Literal["open", "folder"]):
    file_dialog = getattr(webview_module, "FileDialog", None)
    if file_dialog is None:
        raise RuntimeError("pywebview FileDialog API is unavailable; pywebview>=5.0 is required")

    if dialog_kind == "open":
        dialog = getattr(file_dialog, "OPEN", None)
    elif dialog_kind == "folder":
        dialog = getattr(file_dialog, "FOLDER", None)
    else:
        raise ValueError(f"Unsupported dialog kind: {dialog_kind}")

    if dialog is None:
        raise RuntimeError(f"pywebview FileDialog.{dialog_kind.upper()} is unavailable")
    return dialog


def create_file_dialog(
    window,
    *,
    dialog_kind: Literal["open", "folder"],
    directory: str | None = None,
    allow_multiple: bool = False,
    file_types=None,
):
    webview_module = load_webview_runtime()
    dialog = resolve_file_dialog(webview_module, dialog_kind)
    kwargs = {}
    if directory is not None:
        kwargs["directory"] = directory
    if allow_multiple:
        kwargs["allow_multiple"] = True
    if file_types is not None:
        kwargs["file_types"] = file_types
    return window.create_file_dialog(dialog, **kwargs)
