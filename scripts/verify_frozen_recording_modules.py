from __future__ import annotations

import sys
import zipfile
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_frozen_recording_modules.py <build-root>")

    build_root = Path(sys.argv[1])
    lib_root = build_root / "lib"
    library_zip = lib_root / "library.zip"
    zip_names: set[str] = set()
    if library_zip.exists():
        with zipfile.ZipFile(library_zip) as archive:
            zip_names = set(archive.namelist())

    def has_path(relative: str) -> bool:
        relative_path = Path(relative)
        if (lib_root / relative_path).exists():
            return True
        zip_name = "/".join(relative_path.parts)
        return zip_name in zip_names

    checks = {
        "_webrtcvad extension": any(lib_root.glob("_webrtcvad*.pyd")),
        "scipy array api numpy fft": (
            has_path("scipy/_external/array_api_compat/numpy/fft.py")
            or has_path("scipy/_external/array_api_compat/numpy/fft.pyc")
            or has_path("scipy/_lib/array_api_compat/numpy/fft.py")
            or has_path("scipy/_lib/array_api_compat/numpy/fft.pyc")
        ),
    }
    failed = [name for name, ok in checks.items() if not ok]
    for name, ok in checks.items():
        print(f"{name}: {'ok' if ok else 'missing'}")
    if failed:
        raise SystemExit("Missing frozen recording modules: " + ", ".join(failed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
