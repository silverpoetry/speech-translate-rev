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
    }
    for module_path in (
        "common/_aliases",
        "common/_fft",
        "common/_helpers",
        "common/_linalg",
        "common/_typing",
        "numpy/_aliases",
        "numpy/_info",
        "numpy/_typing",
        "numpy/fft",
        "numpy/linalg",
    ):
        checks[f"scipy array api {module_path}"] = any(
            has_path(f"scipy/{root}/array_api_compat/{module_path}.{suffix}")
            for root in ("_external", "_lib")
            for suffix in ("py", "pyc")
        )
    failed = [name for name, ok in checks.items() if not ok]
    for name, ok in checks.items():
        print(f"{name}: {'ok' if ok else 'missing'}")
    if failed:
        raise SystemExit("Missing frozen recording modules: " + ", ".join(failed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
