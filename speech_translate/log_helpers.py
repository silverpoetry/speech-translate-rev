from __future__ import annotations

import logging
from typing import Any


class StdlibLoggerAdapter:
    """Minimal loguru-compatible adapter backed by stdlib logging."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._handler_ids: dict[int, logging.Handler] = {}
        self._next_handler_id = 1

    def debug(self, message: Any) -> None:
        self._logger.debug(message)

    def info(self, message: Any) -> None:
        self._logger.info(message)

    def warning(self, message: Any) -> None:
        self._logger.warning(message)

    def error(self, message: Any) -> None:
        self._logger.error(message)

    def exception(self, message: Any) -> None:
        self._logger.exception(message)

    def log(self, level: str | int, message: Any) -> None:
        if isinstance(level, str):
            resolved = getattr(logging, level.upper(), logging.INFO)
        else:
            resolved = level
        self._logger.log(resolved, message)

    def add(
        self,
        sink: str,
        *,
        level: str = "DEBUG",
        encoding: str = "utf-8",
        backtrace: bool = False,
        diagnose: bool = True,
        format: str | None = None,
    ) -> int:
        _ = (backtrace, diagnose, format)
        handler = logging.FileHandler(sink, encoding=encoding)
        handler.setLevel(getattr(logging, level.upper(), logging.DEBUG))
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s - %(message)s"))
        handler_id = self._next_handler_id
        self._next_handler_id += 1
        self._handler_ids[handler_id] = handler
        self._logger.addHandler(handler)
        return handler_id

    def remove(self, handler_id: int | None = None) -> None:
        if handler_id is None:
            for existing_id in list(self._handler_ids.keys()):
                self.remove(existing_id)
            return
        handler = self._handler_ids.pop(handler_id, None)
        if handler is None:
            return
        self._logger.removeHandler(handler)
        handler.close()


def _build_stdlib_fallback_logger() -> StdlibLoggerAdapter:
    logger = logging.getLogger("speech_translate")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s - %(message)s"))
        logger.addHandler(stream_handler)
    logger.propagate = False
    return StdlibLoggerAdapter(logger)


def get_logger():
    try:
        from loguru import logger as loguru_logger

        return loguru_logger
    except ModuleNotFoundError:
        return _build_stdlib_fallback_logger()


logger = get_logger()


__all__ = ["logger", "get_logger", "StdlibLoggerAdapter"]
