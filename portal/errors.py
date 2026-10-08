"""API errors with a stable machine-readable code and a message safe to show users."""
from __future__ import annotations

from fastapi import HTTPException


class ApiError(HTTPException):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        super().__init__(status_code=status, detail={"error": code, "message": message}, headers=headers)


def unauthorized(message: str = "Sign in to continue.") -> ApiError:
    return ApiError(401, "unauthorized", message, headers={"WWW-Authenticate": "Bearer"})


def forbidden(message: str = "You don't have permission to do that.") -> ApiError:
    return ApiError(403, "forbidden", message)


def not_found(what: str = "Item") -> ApiError:
    return ApiError(404, "not_found", f"{what} not found.")


def conflict(code: str, message: str) -> ApiError:
    return ApiError(409, code, message)


def too_many() -> ApiError:
    return ApiError(429, "rate_limited", "Too many attempts. Wait a few minutes and try again.")
