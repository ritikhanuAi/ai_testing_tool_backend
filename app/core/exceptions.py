from fastapi import FastAPI, Request, HTTPException, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import logging
from app.core.config import settings

logger = logging.getLogger("app.core.exceptions")

class PlatformException(Exception):
    """Base exception for all domain errors."""
    def __init__(self, message: str, code: str = "PLATFORM_ERROR", status_code: int = status.HTTP_400_BAD_REQUEST):
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)

class ScopeViolationException(PlatformException):
    """Raised when scan target URL is not permitted by allowlist."""
    def __init__(self, message: str = "Target domain is not in authorized allowlist"):
        super().__init__(message, code="SCOPE_VIOLATION", status_code=status.HTTP_403_FORBIDDEN)

class UnauthorizedRiskException(PlatformException):
    """Raised when a test case attempts a state-changing action without explicit permission."""
    def __init__(self, message: str = "State-changing test step requires explicit write authorization"):
        super().__init__(message, code="UNAUTHORIZED_RISK", status_code=status.HTTP_403_FORBIDDEN)

class SchemaValidationException(PlatformException):
    """Raised when uploaded test pack YAML fails pre-flight validation."""
    def __init__(self, message: str = "Invalid test pack schema", errors: list = None):
        self.errors = errors or []
        super().__init__(message, code="INVALID_SCHEMA", status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)

class EntityNotFoundException(PlatformException):
    """Raised when a requested resource is not found."""
    def __init__(self, entity_name: str, identifier: str):
        super().__init__(f"{entity_name} with identifier '{identifier}' was not found", code="NOT_FOUND", status_code=status.HTTP_404_NOT_FOUND)

async def platform_exception_handler(request: Request, exc: PlatformException) -> JSONResponse:
    """Format domain exceptions into standard JSON error envelope."""
    logger.warning(f"Domain error [{exc.code}] on {request.method} {request.url.path}: {exc.message}")
    payload = {
        "success": False,
        "error": {
            "code": exc.code,
            "message": exc.message
        }
    }
    if hasattr(exc, "errors") and exc.errors:
        payload["error"]["details"] = exc.errors
    return JSONResponse(status_code=exc.status_code, content=payload)

async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Format standard HTTP exceptions into unified error envelope."""
    logger.warning(f"HTTP {exc.status_code} on {request.method} {request.url.path}: {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": {
                "code": f"HTTP_{exc.status_code}",
                "message": exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            }
        }
    )

async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Format Pydantic schema validation errors into unified error envelope."""
    logger.warning(f"Validation error on {request.method} {request.url.path}: {exc.errors()}")
    # Simplify error structure for frontend consumption
    clean_errors = []
    for err in exc.errors():
        loc = " -> ".join(str(p) for p in err.get("loc", []))
        clean_errors.append({
            "field": loc,
            "message": err.get("msg"),
            "type": err.get("type")
        })
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "success": False,
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Invalid request payload format or parameters",
                "details": clean_errors
            }
        }
    )

async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for unexpected internal server errors."""
    logger.exception(f"Unhandled server error on {request.method} {request.url.path}: {str(exc)}")
    message = str(exc) if settings.DEBUG else "An unexpected internal server error occurred"
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": message
            }
        }
    )

def register_exception_handlers(app: FastAPI) -> None:
    """Register all global error handlers on FastAPI app."""
    app.add_exception_handler(PlatformException, platform_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
