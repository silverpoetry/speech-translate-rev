import os
import re
import sys
from time import strftime

from speech_translate.log_helpers import logger

from ._constants import LOG_FORMAT
from ._path import dir_log

# ------------------ #
FILE_ID = None
CONSOLE_ID = None
recent_stderr = []
current_log: str = f"{strftime('%Y-%m-%d %H-%M-%S')}.log"
ACTIVE_LOG_DIR: str = dir_log
_FALLBACK_STREAMS: list[object] = []
_ORIGINAL_STDERR = sys.stderr


def _ensure_log_dir(path: str) -> str:
    resolved = os.path.abspath(path or dir_log)
    if not os.path.exists(resolved):
        try:
            os.makedirs(resolved)
        except Exception as exc:
            logger.exception(exc)
            logger.error("Error: Cannot create log folder")
    return resolved


def _resolve_log_file_path(log_dir: str | None = None) -> str:
    target_dir = _ensure_log_dir(log_dir or ACTIVE_LOG_DIR)
    return os.path.join(target_dir, current_log)


ACTIVE_LOG_DIR = _ensure_log_dir(ACTIVE_LOG_DIR)


def shorten_progress_bar(match):
    percentage = match.group(1)
    percent_bar = "#" * len(percentage)  # make it a bit longer
    return f"{percentage} | {percent_bar} |"


class StreamStderrToLogger(object):
    """
    For stderr and tqdm progress bar
    """
    def __init__(self):
        # tqdm use stderr to print, so we can consider it as info
        self.considered_info = [
            "Downloading", "Fetching", "run_threaded", "Estimating duration from bitrate", "Translating", "Refine", "Align",
            "Running", "done", "Using cache found in", "%|#", "0%|", "model.bin", "Extracting", "Download"
        ]
        # Progress-like lines that often come from tqdm/huggingface should not be logged as ERROR.
        self.progress_patterns = [
            re.compile(r"\b\d{1,3}%\|"),
            re.compile(r"\b\d+(?:\.\d+)?[kMGT]?i?B/s\b", re.IGNORECASE),
            re.compile(r"\?B/s\b", re.IGNORECASE),
            re.compile(r"\b\d+(?:\.\d+)?[kMGT]?i?B/\d+(?:\.\d+)?[kMGT]?i?B\b", re.IGNORECASE),
            re.compile(r"\[\d{2}:\d{2}<\d{2}:\d{2}"),
            re.compile(r"\[\d{2}:\d{2},\s*\?B/s\]", re.IGNORECASE),
        ]
        self.progress_value_pattern = re.compile(r"\b(?P<value>\d{1,3}(?:\.\d+)?)%\|")

    def _is_progress_line(self, line: str) -> bool:
        return any(p.search(line) for p in self.progress_patterns)

    @staticmethod
    def _extract_progress_value(line: str):
        match = re.search(r"\b(?P<value>\d{1,3}(?:\.\d+)?)%\|", line)
        if match is None:
            return None
        try:
            return float(match.group("value"))
        except Exception:
            return None

    def write(self, buf):
        for line in buf.rstrip().splitlines():
            line = line.rstrip().replace("\x1B[A", "")

            # checking if line is empty. exception use ^ ~ to point out the error
            # but we don't need it in logger because logger is per line
            check_empty = line.replace("^", "").replace("~", "").strip()
            if len(check_empty) == 0:
                continue

            # check where is it from. if keywords from considered_info is in the line then log as info
            if any(x in line for x in self.considered_info) or self._is_progress_line(line):
                shorten = re.sub(r"(\d+%)(\s*)\|(.+?)\|", shorten_progress_bar, line)
                logger.log("INFO", shorten)
                recent_stderr.append(shorten)

                # limit to max 10
                if len(recent_stderr) > 10:
                    recent_stderr.pop(0)
            else:
                try:
                    logger.error(line)
                    # if fail for some reason, just ignore
                except OSError:
                    pass
                except Exception:
                    pass

    def flush(self):
        pass


def _is_loguru_logger() -> bool:
    return hasattr(logger, "_core") and callable(getattr(logger, "remove", None))


def _ensure_writable_stream(stream):
    if stream is not None:
        try:
            stream.write("")
            flush = getattr(stream, "flush", None)
            if callable(flush):
                flush()
            return stream
        except Exception:
            pass

    fallback = open(os.devnull, "w", encoding="utf-8", buffering=1)
    _FALLBACK_STREAMS.append(fallback)
    return fallback


def _configure_loguru_sinks(level: str, log_dir: str | None = None) -> None:
    global CONSOLE_ID, FILE_ID, ACTIVE_LOG_DIR
    ACTIVE_LOG_DIR = _ensure_log_dir(log_dir or ACTIVE_LOG_DIR)

    safe_stderr = _ensure_writable_stream(_ORIGINAL_STDERR)
    logger.remove()
    CONSOLE_ID = logger.add(
        safe_stderr,
        level=level,
        backtrace=False,
        diagnose=False,
        format=LOG_FORMAT,
    )
    FILE_ID = logger.add(
        _resolve_log_file_path(ACTIVE_LOG_DIR),
        level=level,
        encoding="utf-8",
        backtrace=False,
        diagnose=True,
        format=LOG_FORMAT,
    )


def _configure_file_sink(level: str, log_dir: str | None = None) -> None:
    global FILE_ID, ACTIVE_LOG_DIR
    ACTIVE_LOG_DIR = _ensure_log_dir(log_dir or ACTIVE_LOG_DIR)
    if FILE_ID is not None:
        logger.remove(FILE_ID)
    FILE_ID = logger.add(
        _resolve_log_file_path(ACTIVE_LOG_DIR),
        level=level,
        encoding="utf-8",
        backtrace=False,
        diagnose=True,
        format=LOG_FORMAT,
    )


def init_logging(level, log_dir: str | None = None):
    if _is_loguru_logger():
        _configure_loguru_sinks(level, log_dir)
    else:
        _configure_file_sink(level, log_dir)

    sys.stderr = StreamStderrToLogger()
    # tqdm use stderr so we also need to redirect it


def change_log_level(level: str, log_dir: str | None = None):
    if _is_loguru_logger():
        _configure_loguru_sinks(level, log_dir)
    else:
        _configure_file_sink(level, log_dir)


def clear_current_log_file(log_dir: str | None = None, level: str = "DEBUG"):
    global FILE_ID
    target_path = _resolve_log_file_path(log_dir)
    if FILE_ID is not None:
        logger.remove(FILE_ID)
    with open(target_path, "w", encoding="utf-8") as f:
        f.write("")
    _configure_file_sink(level, os.path.dirname(target_path))
