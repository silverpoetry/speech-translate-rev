import sys
from os import environ
from pathlib import Path
from time import perf_counter
from warnings import simplefilter

# Support running as a module and as a direct script in debugger.
if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from speech_translate._constants import APP_USER_MODEL_ID, LOG_FORMAT
else:
    from ._constants import APP_USER_MODEL_ID, LOG_FORMAT


_NULL_STREAMS = []


def _ensure_standard_streams():
    """Make pythonw launches safe for libraries expecting stdio streams."""
    import os

    for stream_name in ("stdin", "stdout", "stderr"):
        if getattr(sys, stream_name, None) is None:
            mode = "r" if stream_name == "stdin" else "w"
            handle = open(os.devnull, mode, encoding="utf-8", buffering=1)
            setattr(sys, stream_name, handle)
            _NULL_STREAMS.append(handle)


def _set_windows_app_user_model_id():
    """Assign a dedicated taskbar identity for source-based pythonw launches."""
    if sys.platform != "win32":
        return

    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


_ensure_standard_streams()
_set_windows_app_user_model_id()


def _run_recording_import_smoke_test() -> int:
    import importlib

    import speech_translate.utils.audio.audio  # noqa: F401

    required_modules = (
        "scipy.signal",
        "scipy._external.array_api_compat",
        "scipy._external.array_api_compat.numpy",
        "scipy._external.array_api_compat.numpy.fft",
        "webrtcvad",
        "_webrtcvad",
    )
    for module_name in required_modules:
        importlib.import_module(module_name)
    return 0


if "--smoke-test-recording-imports" in sys.argv or environ.get("SPEECH_TRANSLATE_SMOKE_TEST_RECORDING_IMPORTS") == "1":
    import os

    os._exit(_run_recording_import_smoke_test())

# override loguru default format so we dont need to do logger.remove on the logger init
environ["LOGURU_FORMAT"] = LOG_FORMAT

# If frozen, stdout will not work because there is no console. So we need to replace stdout
# with stderr so that any module that uses stdout will not break the app
if getattr(sys, "frozen", False):
    sys.stdout = sys.stderr

# supress general user warning like in pytorch
simplefilter("ignore", category=UserWarning)

_bootstrap_t0 = perf_counter()
if __package__ in (None, ""):
    from speech_translate.webview_app import main  # pylint: disable=wrong-import-position
else:
    from .webview_app import main  # pylint: disable=wrong-import-position
_bootstrap_import_ms = int((perf_counter() - _bootstrap_t0) * 1000)
if sys.stderr is not None:
    sys.stderr.write(f"[Bootstrap] import webview_app took {_bootstrap_import_ms}ms\n")

if __name__ == "__main__":
    main()
