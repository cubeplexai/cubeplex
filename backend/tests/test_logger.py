"""Tests for logging module"""

import logging
import tempfile
from pathlib import Path

from cubeplex.utils.logger import (
    init_logging,
    log_agent_creation,
    log_error,
    log_task_completion,
    log_task_start,
    log_tool_call,
    log_tool_result,
    setup_logger,
)


def test_setup_logger() -> None:
    """Test logger setup with console and file output"""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = Path(tmpdir) / "test.log"
        logger = setup_logger(
            "test_logger",
            log_level=logging.INFO,
            log_file=str(log_file),
            console_output=False,
        )

        assert logger is not None
        assert logger.name == "test_logger"
        assert logger.level == logging.INFO


def test_init_logging() -> None:
    """Test application logging initialization"""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = Path(tmpdir) / "app.log"
        init_logging(
            log_level=logging.INFO,
            log_file=str(log_file),
            console_output=False,
        )

        root_logger = logging.getLogger()
        assert root_logger.level == logging.INFO


def test_log_agent_creation() -> None:
    """Test agent creation logging"""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = Path(tmpdir) / "test.log"
        logger = setup_logger(
            "test_agent",
            log_level=logging.INFO,
            log_file=str(log_file),
            console_output=False,
        )

        log_agent_creation(logger, "agent-001", "Test Agent", "gpt-4")

        # Verify log file was created and contains the message
        assert log_file.exists()
        content = log_file.read_text()
        assert "Agent created" in content
        assert "agent-001" in content


def test_log_task_start() -> None:
    """Test task start logging"""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = Path(tmpdir) / "test.log"
        logger = setup_logger(
            "test_task",
            log_level=logging.INFO,
            log_file=str(log_file),
            console_output=False,
        )

        log_task_start(logger, "task-001", "agent-001", "Calculate 2 + 3")

        assert log_file.exists()
        content = log_file.read_text()
        assert "Task started" in content
        assert "task-001" in content


def test_log_tool_call() -> None:
    """Test tool call logging"""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = Path(tmpdir) / "test.log"
        logger = setup_logger(
            "test_tool",
            log_level=logging.DEBUG,
            log_file=str(log_file),
            console_output=False,
        )

        log_tool_call(logger, "calculator", {"expression": "2 + 3"})

        assert log_file.exists()
        content = log_file.read_text()
        assert "Tool called" in content
        assert "calculator" in content


def test_log_tool_result() -> None:
    """Test tool result logging"""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = Path(tmpdir) / "test.log"
        logger = setup_logger(
            "test_tool_result",
            log_level=logging.DEBUG,
            log_file=str(log_file),
            console_output=False,
        )

        log_tool_result(logger, "calculator", "5")

        assert log_file.exists()
        content = log_file.read_text()
        assert "Tool result" in content
        assert "calculator" in content


def test_log_task_completion() -> None:
    """Test task completion logging"""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = Path(tmpdir) / "test.log"
        logger = setup_logger(
            "test_completion",
            log_level=logging.INFO,
            log_file=str(log_file),
            console_output=False,
        )

        log_task_completion(logger, "task-001", 1.5, "Calculation completed")

        assert log_file.exists()
        content = log_file.read_text()
        assert "Task completed" in content
        assert "task-001" in content


def test_log_error() -> None:
    """Test error logging with stack trace"""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = Path(tmpdir) / "test.log"
        logger = setup_logger(
            "test_error",
            log_level=logging.ERROR,
            log_file=str(log_file),
            console_output=False,
        )

        try:
            raise ValueError("Test error")
        except ValueError:
            log_error(logger, "TEST_ERROR", "An error occurred", exc_info=True)

        assert log_file.exists()
        content = log_file.read_text()
        assert "Error occurred" in content
        assert "TEST_ERROR" in content
