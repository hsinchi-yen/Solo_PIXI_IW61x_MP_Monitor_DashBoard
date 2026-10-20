"""One error shape for every endpoint:
{"error": <English message>, "error_zh": <中文 message>, "detail": <English message>, "code": <machine code>}.

Messages are written as Msg("English", "中文"). `detail` keeps the FastAPI field the
existing dashboard already reads. ApiError is plain Python so services and unit tests
can raise it without FastAPI; install() wires the handlers into the app.
"""
import logging

log = logging.getLogger("iw.api")


class Msg(str):
    """A user-facing message: the str value is English, `.zh` is Traditional Chinese."""

    def __new__(cls, en, zh):
        obj = super().__new__(cls, en)
        obj.zh = zh
        return obj


class ApiError(Exception):
    def __init__(self, status, message, code="error", **extra):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.extra = extra


def bad_request(message, code="bad_request", **extra):
    return ApiError(400, message, code, **extra)


def not_found(message=None, code="not_found"):
    return ApiError(404, message or Msg("Not found", "找不到資料"), code)


def error_body(message, code="error", **extra):
    body = {"error": str(message), "detail": str(message), "code": code, **extra}
    zh = getattr(message, "zh", None)
    if zh:
        body["error_zh"] = zh
    return body


def install(app):
    from fastapi import Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError):
        return JSONResponse(error_body(exc.message, exc.code, **exc.extra), status_code=exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException):
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}.get(
            exc.status_code, "error")
        return JSONResponse(error_body(str(exc.detail), code), status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError):
        fields = [".".join(str(p) for p in e.get("loc", [])[1:]) or "body" for e in exc.errors()]
        names = ", ".join(fields)
        return JSONResponse(error_body(Msg(f"Invalid parameters: {names}", f"參數格式不正確：{names}"), "validation"),
                            status_code=400)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        # Never echo exception text (it can carry DB host/user); the log has the details.
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(error_body(Msg("Server error. Please try again later.", "伺服器發生錯誤，請稍後再試"),
                                       "internal"), status_code=500)
