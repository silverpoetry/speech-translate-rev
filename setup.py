from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py


class build_py(_build_py):
    """Keep vendored silero-vad packaging to its runtime payload."""

    def build_package_data(self):
        super().build_package_data()
        silero_root = Path(self.build_lib) / "speech_translate" / "assets" / "silero-vad"
        for relative in (".git", ".github", "examples", "silero-vad.ipynb"):
            target = silero_root / relative
            if target.is_dir():
                self.announce(f"removing package data directory {target}", level=2)
                self._delete_path(target)
            elif target.exists():
                self.announce(f"removing package data file {target}", level=2)
                target.unlink()

    def _delete_path(self, path):
        for child in path.iterdir():
            if child.is_dir():
                self._delete_path(child)
            else:
                child.unlink()
        path.rmdir()


setup(cmdclass={"build_py": build_py})
