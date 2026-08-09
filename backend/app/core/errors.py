"""One exception hierarchy. Public message goes to the client; detail goes to logs only."""

from __future__ import annotations


class AppError(Exception):
    code: str = "internal_error"
    http_status: int = 500
    public_message: str = "An unexpected error occurred."

    def __init__(self, detail: str = "", public_message: str | None = None) -> None:
        self.internal_detail = detail
        if public_message:
            self.public_message = public_message
        super().__init__(detail or self.public_message)


class AuthenticationError(AppError):
    code = "authentication_failed"
    http_status = 401
    public_message = "Authentication failed."


class AuthorizationError(AppError):
    code = "not_authorized"
    http_status = 403
    public_message = "You are not authorized to perform this action."


class ValidationError(AppError):
    code = "invalid_request"
    http_status = 422
    public_message = "The request was invalid."


class NotFoundError(AppError):
    code = "not_found"
    http_status = 404
    public_message = "Resource not found."


class RateLimitError(AppError):
    code = "rate_limited"
    http_status = 429
    public_message = "Too many requests. Please retry shortly."


class PayloadTooLargeError(AppError):
    code = "payload_too_large"
    http_status = 413
    public_message = "The request body is too large."


class UnsupportedMediaTypeError(AppError):
    code = "unsupported_media_type"
    http_status = 415
    public_message = "This file type is not supported."


class GuardrailError(AppError):
    """Raised when a guardrail blocks. Never caught-and-continued."""

    code = "guardrail_blocked"
    http_status = 400
    public_message = "The request was blocked by a safety check."


class TenancyError(AppError):
    """A query was built without tenant scope. Fail closed, always."""

    code = "tenancy_violation"
    http_status = 500
    public_message = "An unexpected error occurred."


class UpstreamError(AppError):
    code = "upstream_failure"
    http_status = 503
    public_message = "An upstream service is unavailable."


class UpstreamPermanentError(UpstreamError):
    """Gateway wrapped an upstream 4xx as a 5xx — retrying will never help."""

    code = "upstream_permanent"


class BudgetExceededError(AppError):
    code = "budget_exceeded"
    http_status = 429
    public_message = "This request exceeded its processing budget."
