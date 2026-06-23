import os
import shutil
import subprocess
import sys
from importlib.util import find_spec
from importlib.metadata import version as get_version
from importlib.metadata import PackageNotFoundError
from pathlib import Path

from cx_Freeze import Executable, setup

sys.setrecursionlimit(5000)
ROOT = Path(__file__).resolve().parent
SITE_PACKAGES = Path(sys.prefix) / "Lib" / "site-packages"
OPEN_OUTPUT = "--open" in sys.argv
if OPEN_OUTPUT:
    sys.argv.remove("--open")


def get_env_name():
    return os.path.basename(sys.prefix)


def app_version():
    with open(ROOT / "speech_translate" / "_version.py", encoding="utf-8") as f_ver:
        return f_ver.readline().split("=")[1].strip().strip('"').strip("'")


# If you get cuda error try to remove your cuda from your system path because cx_freeze will try to include it from there
# instead of the one in the python folder
print(">> Building Speech Translate Rev version", app_version())
print(">> Environment:", get_env_name())

if "build_exe" in sys.argv:
    print(">> Running build_patch.py")
    subprocess.check_call([sys.executable, str(ROOT / "build_patch.py")])
    print(">> Done")


def clear_dir(_dir):
    print(">> Clearing", _dir)
    try:
        if not os.path.exists(_dir):
            return
        if os.path.isfile(_dir):
            os.remove(_dir)
        else:
            # remove all files or folders in the dir
            for f_get in os.listdir(_dir):
                try:
                    shutil.rmtree(os.path.join(_dir, f_get))
                except Exception:
                    os.remove(os.path.join(_dir, f_get))
    except Exception as e:
        print(f">> Failed to clear {_dir} reason: {e}")


def remove_path(path: Path):
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def prune_silero_runtime_assets(build_root: Path):
    silero_root = build_root / "lib" / "speech_translate" / "assets" / "silero-vad"
    if not silero_root.exists():
        return
    for relative in (".git", ".github", "examples", "silero-vad.ipynb", "CODE_OF_CONDUCT.md"):
        target = silero_root / relative
        if target.exists():
            print(">> Removing non-runtime silero asset", target)
            remove_path(target)


def prune_packaged_runtime_state(build_root: Path):
    app_root = build_root / "lib" / "speech_translate"
    for relative in ("_user", "debug", "export", "log", "temp"):
        target = app_root / relative
        if target.exists():
            print(">> Removing packaged runtime state", target)
            remove_path(target)


def assert_no_packaged_runtime_state(build_root: Path):
    app_root = build_root / "lib" / "speech_translate"
    runtime_state = ("_user", "debug", "export", "log", "temp")
    leftovers = [str(app_root / relative) for relative in runtime_state if (app_root / relative).exists()]
    if leftovers:
        joined = "\n".join(leftovers)
        raise RuntimeError(f"Packaged runtime state directories were not removed:\n{joined}")


def ensure_scipy_external_array_api_alias(build_root: Path):
    scipy_root = build_root / "lib" / "scipy"
    source = scipy_root / "_lib" / "array_api_compat"
    target_root = scipy_root / "_external"
    target = target_root / "array_api_compat"
    if not source.exists() or target.exists():
        return

    print(">> Creating scipy._external.array_api_compat compatibility package")
    target_root.mkdir(parents=True, exist_ok=True)
    init_file = target_root / "__init__.py"
    if not init_file.exists():
        init_file.write_text("", encoding="utf-8")
    shutil.copytree(source, target, dirs_exist_ok=True)


def get_whisper_version():
    try:
        ver = get_version("openai-whisper")
    except PackageNotFoundError:
        print(">> openai-whisper metadata not found; skipping whisper dist-info copy")
        return ""
    print(">> Getting whisper version")
    print(">> Whisper version:", ver)
    return ver


def optional_include(module_name: str) -> list[str]:
    try:
        return [module_name] if find_spec(module_name) is not None else []
    except ModuleNotFoundError:
        return []


if "build_exe" in sys.argv:
    print(">> Clearing transient folders")
    clear_dir(str(ROOT / "speech_translate" / "export"))
    clear_dir(str(ROOT / "speech_translate" / "debug"))
    clear_dir(str(ROOT / "speech_translate" / "log"))
    clear_dir(str(ROOT / "speech_translate" / "temp"))
    clear_dir(str(ROOT / "speech_translate" / "assets" / "silero-vad" / "__pycache__"))
    print(">> Done")

folder_name = f"build/SpeechTranslateRev {app_version()} {get_env_name()}"

print("ROOT:", ROOT)
print("Assets:", ROOT / "speech_translate" / "assets")

asset_root = ROOT / "speech_translate" / "assets"
include_files = [
    (str(asset_root / "NotoEmoji-Bold.ttf"), "lib/speech_translate/assets/NotoEmoji-Bold.ttf"),
    (str(asset_root / "awd.mp3"), "lib/speech_translate/assets/awd.mp3"),
    (str(asset_root / "base_hallucination_filter.json"), "lib/speech_translate/assets/base_hallucination_filter.json"),
    (str(asset_root / "beep.mp3"), "lib/speech_translate/assets/beep.mp3"),
    (str(asset_root / "icon.ico"), "lib/speech_translate/assets/icon.ico"),
    (str(asset_root / "icon.png"), "lib/speech_translate/assets/icon.png"),
    (str(asset_root / "parameter.txt"), "lib/speech_translate/assets/parameter.txt"),
    (str(asset_root / "readme.txt"), "lib/speech_translate/assets/readme.txt"),
    (str(asset_root / "silero-vad" / "LICENSE"), "lib/speech_translate/assets/silero-vad/LICENSE"),
    (str(asset_root / "silero-vad" / "README.md"), "lib/speech_translate/assets/silero-vad/README.md"),
    (str(asset_root / "silero-vad" / "hubconf.py"), "lib/speech_translate/assets/silero-vad/hubconf.py"),
    (str(asset_root / "silero-vad" / "utils_vad.py"), "lib/speech_translate/assets/silero-vad/utils_vad.py"),
    (str(asset_root / "silero-vad" / "files"), "lib/speech_translate/assets/silero-vad/files"),
]

build_exe_options = {
    "excludes": ["yapf", "ruff", "cx_Freeze", "pylint", "isort"],
    "includes": [
        "webrtcvad",
        "_webrtcvad",
        *optional_include("scipy._lib.array_api_compat"),
        *optional_include("scipy._lib.array_api_compat.common._fft"),
        *optional_include("scipy._lib.array_api_compat.common._linalg"),
        *optional_include("scipy._lib.array_api_compat.numpy"),
        *optional_include("scipy._lib.array_api_compat.numpy.fft"),
        *optional_include("scipy._lib.array_api_compat.numpy.linalg"),
        *optional_include("scipy._external.array_api_compat"),
        *optional_include("scipy._external.array_api_compat.common._fft"),
        *optional_include("scipy._external.array_api_compat.common._linalg"),
        *optional_include("scipy._external.array_api_compat.numpy"),
        *optional_include("scipy._external.array_api_compat.numpy.fft"),
        *optional_include("scipy._external.array_api_compat.numpy.linalg"),
    ],
    "packages": [
        "torch",
        "soundfile",
        "sounddevice",
        "av",
        "stable_whisper",
        "faster_whisper",
        "whisper",
        "webview",
    ],
    "build_exe": folder_name,
    "include_msvcr": True,
    "include_files": include_files,
}

BASE = "gui" if sys.platform == "win32" else None

setup(
    name="SpeechTranslateRev",
    version=app_version(),
    description="Speech Translate Rev",
    options={
        "build_exe": build_exe_options,
    },
    executables=[
        Executable(
            "Run.py",
            base=BASE,
            icon="speech_translate/assets/icon.ico",
            target_name="SpeechTranslateRev.exe",
        )
    ],
)

# check if arg is build_exe
if len(sys.argv) < 2 or sys.argv[1] != "build_exe":
    sys.exit(0)

print(">> Copying some more files...")
prune_silero_runtime_assets(Path(folder_name))
prune_packaged_runtime_state(Path(folder_name))
assert_no_packaged_runtime_state(Path(folder_name))
ensure_scipy_external_array_api_alias(Path(folder_name))

# we need to copy av.libs to foldername/lib because cx_freeze doesn't copy it for some reason
print(">> Copying av.libs to lib folder")
av_libs = SITE_PACKAGES / "av.libs"
if av_libs.exists():
    shutil.copytree(av_libs, Path(folder_name) / "lib" / "av.libs", dirs_exist_ok=True)
else:
    print(">> av.libs not found; skipping")

# we also need to copy openai_whisper-{version}.dist-info to foldername/lib because cx_freeze doesn't copy it
whisper_version = get_whisper_version()
if whisper_version:
    print(">> Copying whisper metadata to lib folder")
    shutil.copytree(
        SITE_PACKAGES / f"openai_whisper-{whisper_version}.dist-info",
        Path(folder_name) / "lib" / f"openai_whisper-{whisper_version}.dist-info",
        dirs_exist_ok=True,
    )

# copy LICENSE as license.txt to build folder
print(">> Creating license.txt to build folder")
with open(ROOT / "LICENSE", "r", encoding="utf-8") as f:
    with open(Path(folder_name) / "license.txt", "w", encoding="utf-8") as f2:
        f2.write(f.read())

# copy README.md as README.txt to build folder
print(">> Creating README.txt to build folder")
with open(ROOT / "packaging" / "windows" / "pre_install_note.txt", "r", encoding="utf-8") as f:
    with open(Path(folder_name) / "README.txt", "w", encoding="utf-8") as f2:
        f2.write(f.read())

# create version.txt
print(">> Creating version.txt")
with open(Path(folder_name) / "version.txt", "w", encoding="utf-8") as f:
    f.write(app_version())

# create link to repo
print(">> Creating link to repo")
with open(Path(folder_name) / "homepage.url", "w", encoding="utf-8") as f:
    f.write("[InternetShortcut]\n")
    f.write("URL=https://github.com/silverpoetry/speech-translate-rev")

if OPEN_OUTPUT:
    print(">> Opening output folder")
    output_folder = os.path.abspath(folder_name)
    try:
        os.startfile(output_folder)
    except Exception:
        subprocess.call(["xdg-open", output_folder])
