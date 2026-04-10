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
    from speech_translate._constants import LOG_FORMAT
else:
    from ._constants import LOG_FORMAT

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
sys.stderr.write(f"[Bootstrap] import webview_app took {_bootstrap_import_ms}ms\n")

if __name__ == "__main__":
    main()
