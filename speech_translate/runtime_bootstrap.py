from __future__ import annotations

import os
import subprocess
import sys
from platform import system
from typing import Any, Dict


_whisper_load_api = None


def get_whisper_load_api():
    global _whisper_load_api
    if _whisper_load_api is None:
        from speech_translate.utils.whisper import load as whisper_load

        _whisper_load_api = whisper_load
    return _whisper_load_api


def prepare_subprocess_kwargs(kwargs: Dict[str, Any], platform_name: str | None = None) -> Dict[str, Any]:
    prepared = dict(kwargs)
    if (platform_name or system()) == "Windows" and "startupinfo" not in prepared:
        prepared["startupinfo"] = subprocess.STARTUPINFO()
        prepared["startupinfo"].dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return prepared


class NoConsolePopen(subprocess.Popen):
    """Disable console windows when spawning subprocesses on Windows."""

    def __init__(self, args, **kwargs):
        super().__init__(args, **prepare_subprocess_kwargs(kwargs))


def install_no_console_popen() -> None:
    if subprocess.Popen is not NoConsolePopen:
        subprocess.Popen = NoConsolePopen


def add_ffmpeg_to_path(weak: bool = False) -> bool:
    """Add ffmpeg executables to PATH."""
    if getattr(sys, "frozen", False):
        from static_ffmpeg import _add_paths, run

        run.sys.stdout = sys.stderr
        if weak:
            has_ffmpeg = _add_paths._has("ffmpeg") is not None
            has_ffprobe = _add_paths._has("ffprobe") is not None
            if has_ffmpeg and has_ffprobe:
                return False

        ffmpeg, _ = run.get_or_fetch_platform_executables_else_raise()
        os.environ["PATH"] = os.pathsep.join([os.path.dirname(ffmpeg), os.environ["PATH"]])
        return True

    from static_ffmpeg import _add_paths

    return _add_paths.add_paths()
