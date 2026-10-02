"""Typed API errors so every failure carries a stable machine-readable code."""
from __future__ import annotations

from fastapi import HTTPException


class ApiError(HTTPException):
    def __init__(self, status_code: int, error: str, message: str):
        super().__init__(status_code=status_code, detail={"error": error, "message": message})
        self.error = error
        self.message = message


def unauthorized(message: str = "認証が必要です。") -> ApiError:
    return ApiError(401, "unauthorized", message)


def forbidden(error: str, message: str) -> ApiError:
    return ApiError(403, error, message)


def not_found(message: str = "見つかりませんでした。") -> ApiError:
    return ApiError(404, "not_found", message)


def bad_request(error: str, message: str) -> ApiError:
    return ApiError(400, error, message)


def conflict(error: str, message: str) -> ApiError:
    return ApiError(409, error, message)