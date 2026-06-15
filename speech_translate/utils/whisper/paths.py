from __future__ import annotations

import os


def get_default_download_root() -> str:
    """Get the default download root."""
    return os.getenv("XDG_CACHE_HOME", os.path.join(os.path.expanduser("~"), ".cache", "whisper"))
