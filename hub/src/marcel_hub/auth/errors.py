"""Every error leaves the hub in the contract's `Error` shape: a stable `code` and a `message`
written for the person holding the phone."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(
        self, status: int, code: str, message: str, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers


def error_response(
    status: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse({'code': code, 'message': message}, status_code=status, headers=headers)


_HTTP_MESSAGES = {
    404: ('not_found', 'There is nothing at that address.'),
    405: ('method_not_allowed', 'That address does not take this kind of request.'),
}


def _field(loc: tuple[Any, ...] | list[Any]) -> str:
    parts = [str(p) for p in loc if p not in ('body', 'query', 'path', 'header')]
    return '.'.join(parts) or 'the request body'


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return error_response(exc.status, exc.code, exc.message, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_request: Request, exc: RequestValidationError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        first = exc.errors()[0]
        msg = str(first.get('msg', 'is not valid')).rstrip('.')
        return error_response(400, 'bad_request', f'Check `{_field(first.get("loc", ()))}`: {msg}.')

    @app.exception_handler(StarletteHTTPException)
    async def _http(_request: Request, exc: StarletteHTTPException) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        code, message = _HTTP_MESSAGES.get(
            exc.status_code, (f'http_{exc.status_code}', 'The hub could not do that.')
        )
        return error_response(exc.status_code, code, message, dict(exc.headers or {}))
