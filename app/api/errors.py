"""Safe public errors. Do not serialize exceptions, SQL parameters or upload paths."""
import logging
from uuid import uuid4
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from fastapi.responses import JSONResponse
from app.schemas.api import ErrorResponse

logger = logging.getLogger(__name__)


class APIError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code, self.code, self.message = status_code, code, message
        super().__init__(message)


def error_response(request: Request, status_code: int, code: str, message: str):
    request_id = getattr(request.state, "request_id", None) or uuid4().hex
    return JSONResponse(status_code=status_code, headers={"X-Request-ID": request_id},
                        content=ErrorResponse(code=code, message=message, request_id=request_id).model_dump())


def install_error_handlers(app):
    @app.exception_handler(APIError)
    async def business_error(request, exc):
        return error_response(request, exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def parameter_error(request, exc):
        if getattr(request.scope.get('route'),'name',None)=='patch_order':
            locations={tuple(error['loc']) for error in exc.errors()}
            if ('body','product','unit_price') in locations:
                return error_response(request,422,'INVALID_PRICE','销售单价须使用十进制字符串，必须为有限正数，最多14位整数、4位小数')
            if ('body','note') in locations:
                return error_response(request,422,'INVALID_NOTE','操作说明 / 修改原因必填，最多500字')
            if ('body','verification_basis') in locations:
                return error_response(request,422,'INVALID_PRICE_BASIS','产品与价格核对依据最多1000字')
        return error_response(request, 422, "INVALID_PARAMETERS", "请求参数不合法或缺失，请检查客户、文件、幂等键及分页参数")

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(request, exc.status_code, "HTTP_ERROR", "请求资源不存在" if exc.status_code == 404 else "请求格式或方法不合法")

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        # Exception text may contain SQL, credentials and paths; log only type + correlation ID.
        request_id = uuid4().hex
        request.state.request_id = request_id
        logger.error("API failure request_id=%s type=%s", request_id, type(exc).__name__)
        return error_response(request, 500, "INTERNAL_ERROR", "服务暂时无法完成处理，请保留请求编号并使用原幂等键重试")
