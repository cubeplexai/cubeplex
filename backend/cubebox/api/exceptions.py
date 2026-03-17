"""API Exception Classes and Error Handlers

Defines custom exception classes for API errors and FastAPI exception handlers.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from loguru import logger

from cubebox.agents.schemas import ErrorEvent


class APIException(Exception):
    """Base exception class for API errors"""

    def __init__(
        self,
        error_code: str,
        message: str,
        status_code: int,
        details: str | None = None,
    ) -> None:
        """Initialize API exception

        Args:
            error_code: Machine-readable error code
            message: Human-readable error message
            status_code: HTTP status code
            details: Optional detailed error information
        """
        self.error_code = error_code
        self.message = message
        self.status_code = status_code
        self.details = details
        super().__init__(self.message)

    def to_error_event(self) -> ErrorEvent:
        """Convert exception to ErrorEvent

        Returns:
            ErrorEvent with error information
        """
        return ErrorEvent(
            type="error",
            timestamp=datetime.now(UTC).isoformat(),
            data={
                "error_code": self.error_code,
                "message": self.message,
                "details": self.details,
            },
        )

    def to_response(self) -> dict[str, Any]:
        """Convert exception to JSON response

        Returns:
            Dictionary with error information
        """
        response: dict[str, Any] = {
            "status": "error",
            "error_code": self.error_code,
            "message": self.message,
        }
        if self.details:
            response["details"] = self.details
        return response


class ResourceNotFoundError(APIException):
    """Exception for resource not found errors (404)"""

    def __init__(self, resource: str, resource_id: str) -> None:
        """Initialize ResourceNotFoundError

        Args:
            resource: Resource type (e.g., "Conversation", "Message")
            resource_id: Resource identifier
        """
        super().__init__(
            error_code="RESOURCE_NOT_FOUND",
            message=f"{resource} not found",
            status_code=status.HTTP_404_NOT_FOUND,
            details=f"{resource} with id '{resource_id}' does not exist",
        )


class InvalidInputError(APIException):
    """Exception for invalid input/validation errors (400)"""

    def __init__(self, message: str, details: str | None = None) -> None:
        """Initialize InvalidInputError

        Args:
            message: Error message
            details: Optional detailed error information
        """
        super().__init__(
            error_code="INVALID_INPUT",
            message=message,
            status_code=status.HTTP_400_BAD_REQUEST,
            details=details,
        )


class ModelNotFoundError(APIException):
    """Exception for missing model (400)"""

    def __init__(self, model_id: str, details: str | None = None) -> None:
        """Initialize ModelNotFoundError

        Args:
            model_id: ID of the missing model
            details: Optional detailed error information
        """
        super().__init__(
            error_code="MODEL_NOT_FOUND",
            message=f"Model '{model_id}' not found",
            status_code=status.HTTP_400_BAD_REQUEST,
            details=details,
        )


class ProviderNotFoundError(APIException):
    """Exception for missing provider (400)"""

    def __init__(self, provider_name: str, details: str | None = None) -> None:
        """Initialize ProviderNotFoundError

        Args:
            provider_name: Name of the missing provider
            details: Optional detailed error information
        """
        super().__init__(
            error_code="PROVIDER_NOT_FOUND",
            message=f"Provider '{provider_name}' not found",
            status_code=status.HTTP_400_BAD_REQUEST,
            details=details,
        )


class ToolNotFoundError(APIException):
    """Exception for missing tool (400)"""

    def __init__(self, tool_name: str, details: str | None = None) -> None:
        """Initialize ToolNotFoundError

        Args:
            tool_name: Name of the missing tool
            details: Optional detailed error information
        """
        super().__init__(
            error_code="TOOL_NOT_FOUND",
            message=f"Tool '{tool_name}' not found",
            status_code=status.HTTP_400_BAD_REQUEST,
            details=details,
        )


class ExecutionError(APIException):
    """Exception for execution failures (500)"""

    def __init__(self, message: str, details: str | None = None) -> None:
        """Initialize ExecutionError

        Args:
            message: Error message
            details: Optional detailed error information
        """
        super().__init__(
            error_code="EXECUTION_ERROR",
            message=message,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            details=details,
        )


class InternalError(APIException):
    """Exception for unexpected internal errors (500)"""

    def __init__(self, message: str, details: str | None = None) -> None:
        """Initialize InternalError

        Args:
            message: Error message
            details: Optional detailed error information
        """
        super().__init__(
            error_code="INTERNAL_ERROR",
            message=message,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            details=details,
        )


async def api_exception_handler(_request: Request, exc: APIException) -> JSONResponse:
    """Handle APIException and return JSON response

    Args:
        _request: FastAPI request object
        exc: APIException instance

    Returns:
        JSONResponse with error information
    """
    # Log the error with stack trace
    logger.error(
        f"API Error: {exc.error_code} - {exc.message}",
        exc_info=True,
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_response(),
    )


async def generic_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Handle generic exceptions and return 500 error

    Args:
        _request: FastAPI request object
        exc: Exception instance

    Returns:
        JSONResponse with error information
    """
    # Log the error with full stack trace
    logger.error(
        f"Unhandled exception: {type(exc).__name__}",
        exc_info=True,
    )

    error_response = {
        "status": "error",
        "error_code": "INTERNAL_ERROR",
        "message": "An unexpected error occurred",
    }

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_response,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register exception handlers with FastAPI app

    Args:
        app: FastAPI application instance
    """
    app.add_exception_handler(
        APIException,
        api_exception_handler,  # type: ignore[arg-type]
    )
    app.add_exception_handler(Exception, generic_exception_handler)
