"""Logging Configuration

Provides centralized logging setup using Python's standard logging module with:
- Console and file output
- Structured logging format
- Proper log levels (INFO/DEBUG)
- Stack trace inclusion for errors
"""

import logging
import logging.handlers
from pathlib import Path

# Log format: [%(asctime)s] [%(name)s] [%(levelname)s] %(message)s
LOG_FORMAT = "[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_log_directory() -> Path:
    """
    Get the log directory path.

    Returns:
        Path to logs directory
    """
    # Get the backend directory (parent of cubeplex)
    backend_dir = Path(__file__).parent.parent.parent
    log_dir = backend_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    return log_dir


def setup_logger(
    name: str,
    log_level: int = logging.INFO,
    log_file: str | None = None,
    console_output: bool = True,
) -> logging.Logger:
    """
    Set up a logger with console and file handlers.

    Args:
        name: Logger name (typically __name__)
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional log file path. If None, uses default logs directory
        console_output: Whether to output to console

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    # Avoid adding duplicate handlers
    if logger.handlers:
        return logger

    # Create formatter
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # Console handler
    if console_output:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    # File handler
    if log_file is None:
        log_file = str(get_log_directory() / "cubeplex.log")

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def init_logging(
    log_level: int = logging.INFO,
    log_file: str | None = None,
    console_output: bool = True,
) -> None:
    """
    Initialize logging for the application.

    Configures root logger and common library loggers.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional log file path
        console_output: Whether to output to console
    """
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create formatter
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # Console handler
    if console_output:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    # File handler
    if log_file is None:
        log_file = str(get_log_directory() / "cubeplex.log")

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Configure common library loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def log_agent_creation(
    logger: logging.Logger,
    agent_id: str,
    name: str,
    model_id: str,
) -> None:
    """
    Log agent creation event.

    Args:
        logger: Logger instance
        agent_id: Agent identifier
        name: Agent name
        model_id: Model identifier
    """
    logger.info(f"Agent created: id={agent_id}, name={name}, model_id={model_id}")


def log_task_start(
    logger: logging.Logger,
    task_id: str,
    agent_id: str,
    task_description: str,
) -> None:
    """
    Log task start event.

    Args:
        logger: Logger instance
        task_id: Task identifier
        agent_id: Agent identifier
        task_description: Task description
    """
    logger.info(
        f"Task started: task_id={task_id}, agent_id={agent_id}, description={task_description}"
    )


def log_tool_call(
    logger: logging.Logger,
    tool_name: str,
    input_params: dict[str, object],
) -> None:
    """
    Log tool call event.

    Args:
        logger: Logger instance
        tool_name: Name of the tool being called
        input_params: Input parameters for the tool
    """
    logger.debug(f"Tool called: tool_name={tool_name}, input={input_params}")


def log_tool_result(
    logger: logging.Logger,
    tool_name: str,
    output: str,
) -> None:
    """
    Log tool result event.

    Args:
        logger: Logger instance
        tool_name: Name of the tool
        output: Tool output/result
    """
    logger.debug(f"Tool result: tool_name={tool_name}, output={output}")


def log_task_completion(
    logger: logging.Logger,
    task_id: str,
    duration: float,
    result_summary: str,
) -> None:
    """
    Log task completion event.

    Args:
        logger: Logger instance
        task_id: Task identifier
        duration: Task duration in seconds
        result_summary: Summary of the result
    """
    logger.info(
        f"Task completed: task_id={task_id}, duration={duration:.2f}s, result={result_summary}"
    )


def log_error(
    logger: logging.Logger,
    error_code: str,
    message: str,
    exc_info: bool = True,
) -> None:
    """
    Log error event with stack trace.

    Args:
        logger: Logger instance
        error_code: Error code identifier
        message: Error message
        exc_info: Whether to include exception info (stack trace)
    """
    logger.error(
        f"Error occurred: error_code={error_code}, message={message}",
        exc_info=exc_info,
    )
