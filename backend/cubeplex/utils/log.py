"""Logging Configuration

Provides centralized logging setup using loguru with:
- Console and file output
- JSON formatting
- Automatic log rotation
- Integration with standard logging
"""

import atexit
import json
import logging
import os
import re
import socket
import sys
from typing import Any

from loguru import logger
from yarl import URL

from cubeplex.config import config


def get_log_path() -> str:
    """Get the log file path based on hostname"""
    hostname = socket.gethostname()
    if getattr(sys, "frozen", False):
        logdir = os.path.dirname(os.path.realpath(sys.executable))
    else:
        logdir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return f"{logdir}/logs/{hostname}.log"


def check_and_replace_angle_brackets(string: str) -> str:
    """Replace angle brackets with square brackets for safe logging"""
    return re.sub(r"<([^>]*)>", r"[\1]", string)


def url_serializer(obj: Any) -> str | None:
    """Serialize URL objects to strings"""
    if isinstance(obj, URL):
        return str(obj)
    return None


def json_formatter(record: dict[str, Any]) -> str:
    """Format log records with color and structure"""
    function_name = check_and_replace_angle_brackets(record["function"])
    message = check_and_replace_angle_brackets(record["message"])
    message = message.replace("{", "{{").replace("}", "}}").replace("\n", " ")

    extra = ""
    if record["extra"]:
        extra = (
            check_and_replace_angle_brackets(
                json.dumps(record["extra"], default=url_serializer, ensure_ascii=False)
            )
            .replace("{", "{{")
            .replace("}", "}}")
        )

    formatting = (
        f"<green>{record['time'].strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}</green> | "
        + f"<level>{record['level'].name: <8}</level> | "
        + f"<cyan>{record['name']}</cyan>:<cyan>{function_name}</cyan>:"
        + f"<cyan>{record['line']}</cyan> | "
        + f"<level>{message}</level>"
    )

    if extra:
        formatting += f" | <level>{extra}</level>\n"
    else:
        formatting += "\n"

    if record["exception"]:
        formatting += "{exception}"

    return formatting


# Third-party libraries that emit large volumes of DEBUG output when the root
# logger is at DEBUG. Capped to `logging.third_party_level` by default; opt
# back into DEBUG via `logging.verbose_modules`.
_NOISY_THIRD_PARTY_LOGGERS = (
    "botocore",
    "aiobotocore",
    "boto3",
    "s3transfer",
    "urllib3",
    "httpcore",
    "httpx",
    "anthropic",
    "openai",
    "opensandbox",
    "mcp.client.streamable_http",
    # lark_oapi's WebSocket transport (long-connection mode) and the
    # underlying `websockets` library log every PING/PONG frame at DEBUG,
    # which floods the log when running with debug on.
    "websockets",
    "lark_oapi",
)


def _apply_third_party_log_levels(root_level: int) -> None:
    """Silence chatty third-party loggers, then re-enable any opted-in modules.

    If `root_level` is already INFO or higher, the silence cap is a no-op
    (third-party loggers already inherit INFO from root). The opt-in
    `verbose_modules` list still works at any root level.
    """
    third_party_name = str(config.get("logging.third_party_level", "WARNING")).upper()
    third_party_level = logging.getLevelName(third_party_name)
    if not isinstance(third_party_level, int):
        third_party_level = logging.WARNING

    # Only raise the floor — never lower a logger below its natural level.
    if third_party_level > root_level:
        for name in _NOISY_THIRD_PARTY_LOGGERS:
            logging.getLogger(name).setLevel(third_party_level)

    verbose_modules = config.get("logging.verbose_modules", []) or []
    for name in verbose_modules:
        logging.getLogger(name).setLevel(logging.DEBUG)


class InterceptHandler(logging.Handler):
    """
    Intercept standard logging and redirect to loguru.

    Allows third-party libraries using standard logging to be captured
    by loguru for consistent formatting and output.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame = sys._getframe(6)
        depth: int = 6

        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back  # type: ignore
            depth += 1

        should_capture_exception = record.exc_info or (
            record.levelno >= logging.ERROR and sys.exc_info()[0] is not None
        )

        logger.opt(depth=depth, exception=should_capture_exception).log(level, record.getMessage())


def init(log_path: str | None = None, debug: bool | None = None) -> None:
    """
    Initialize logging configuration.

    Args:
        log_path: Path to log file. Defaults to get_log_path()
        debug: Enable debug logging. Defaults to config.get("debug", False)
    """
    if log_path is None:
        log_path = config.get("logging.path", None) or get_log_path()
    if debug is None:
        debug = config.get("debug", False)

    # Configure standard logging to use loguru
    loggers = (
        logging.getLogger(name)
        for name in logging.root.manager.loggerDict
        if name.startswith("uvicorn.")
    )
    intercept_handler = InterceptHandler()
    for uvicorn_logger in loggers:
        uvicorn_logger.handlers = []

    level = logging.DEBUG if debug else logging.INFO

    logging.basicConfig(handlers=[intercept_handler], level=level, force=True)
    logging.getLogger("uvicorn.access").handlers = [intercept_handler]
    logging.getLogger("uvicorn").handlers = [intercept_handler]

    _apply_third_party_log_levels(level)

    diagnose = bool(debug)
    logger.configure(
        **{  # type: ignore
            "handlers": [
                {
                    "sink": sys.stdout,
                    "level": level,
                    "catch": True,
                    "diagnose": diagnose,
                    "backtrace": True,
                    "serialize": False,
                    "format": json_formatter,
                },
                {
                    "sink": log_path,
                    "level": level,
                    "serialize": False,
                    "catch": True,
                    "diagnose": diagnose,
                    "backtrace": True,
                    "enqueue": True,
                    "rotation": "00:00",
                    "retention": "30 days",
                    "format": json_formatter,
                },
            ]
        }
    )

    logger.info("Logging initialized", log_path=log_path, debug=debug)


def shutdown() -> None:
    """Flush and stop Loguru handlers created by init()."""
    logger.complete()
    logger.remove()


atexit.register(shutdown)
