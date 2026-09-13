"""FastAPI 应用入口和统一错误边界。"""

from __future__ import annotations

import uuid
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, text

from .config import get_settings
from .database import SessionLocal
from .models import Role, RoleName
from .routers import admin, auth, files, gateway, portal, public
from .services.observability import configure_json_logging, http_logger


configure_json_logging()


def seed_roles() -> None:
    with SessionLocal() as session:
        for role_name, description in ((RoleName.USER.value, "普通平台用户"), (RoleName.ADMIN.value, "平台管理员")):
            if not session.scalar(select(Role).where(Role.name == role_name)):
                session.add(Role(name=role_name, description=description))
        session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings().validate_runtime()
    seed_roles()
    yield


settings = get_settings()
app = FastAPI(
    title="API Gateway 开发者平台",
    version="0.2.0",
    lifespan=lifespan,
    # 生产环境不公开管理接口的 OpenAPI 元数据；开发和测试仍保留便于调试。
    docs_url=None if settings.app_env == "production" else "/docs",
    redoc_url=None if settings.app_env == "production" else "/redoc",
    openapi_url=None if settings.app_env == "production" else "/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token", "X-API-Key", "X-Request-ID"],
)
Path(get_settings().brand_asset_dir).expanduser().resolve().mkdir(parents=True, exist_ok=True)
app.mount("/brand-assets", StaticFiles(directory=get_settings().brand_asset_dir), name="brand-assets")


def _safe_validation_errors(error: RequestValidationError) -> list[dict[str, object]]:
    """清理校验错误中的异常对象，确保统一错误响应一定可 JSON 序列化。"""

    safe_errors: list[dict[str, object]] = []
    for item in error.errors():
        safe_item = dict(item)
        ctx = safe_item.get("ctx")
        if isinstance(ctx, dict):
            safe_ctx = dict(ctx)
            if "error" in safe_ctx:
                safe_ctx["error"] = str(safe_ctx["error"])
            safe_item["ctx"] = safe_ctx
        safe_errors.append(safe_item)
    return safe_errors


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    # 请求标识只由服务端生成，避免客户端伪造 request_id 影响审计链路。
    request.state.request_id = str(uuid.uuid4())
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["x-request-id"] = request.state.request_id
    http_logger.info(
        "http_request_completed",
        extra={
            "event": "http_request",
            "request_id": request.state.request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return response


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, error: HTTPException) -> JSONResponse:
    code_by_status = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        410: "GONE",
        413: "PAYLOAD_TOO_LARGE",
        415: "UNSUPPORTED_MEDIA_TYPE",
        422: "VALIDATION_ERROR",
        423: "ACCOUNT_LOCKED",
        429: "RATE_LIMITED",
        502: "UPSTREAM_UNAVAILABLE",
        503: "SERVICE_UNAVAILABLE",
        504: "UPSTREAM_TIMEOUT",
    }
    detail = error.detail
    code = code_by_status.get(error.status_code, "REQUEST_FAILED")
    message = str(detail)
    details = None
    if isinstance(detail, dict):
        code = str(detail.get("code") or code)
        message = str(detail.get("message") or "请求失败")
        details = detail.get("details")
    content: dict[str, object] = {
        "code": code,
        "message": message,
        "request_id": getattr(request.state, "request_id", "unknown"),
    }
    if details is not None:
        content["details"] = details
    return JSONResponse(status_code=error.status_code, content=content)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, error: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "code": "VALIDATION_ERROR",
            "message": "请求参数不合法",
            "request_id": getattr(request.state, "request_id", "unknown"),
            "details": _safe_validation_errors(error),
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, error: Exception) -> JSONResponse:
    http_logger.exception(
        "http_request_unhandled_error",
        extra={
            "event": "http_error",
            "request_id": getattr(request.state, "request_id", "unknown"),
            "method": request.method,
            "path": request.url.path,
            "status_code": 500,
            "error_code": "INTERNAL_ERROR",
        },
    )
    return JSONResponse(
        status_code=500,
        content={
            "code": "INTERNAL_ERROR",
            "message": "服务内部错误",
            "request_id": getattr(request.state, "request_id", "unknown"),
        },
    )


@app.get("/health", tags=["健康检查"])
def health() -> dict[str, object]:
    settings = get_settings()
    return {"status": "ok", "service": "api-gateway", "environment": settings.app_env, "tomodd_configured": bool(settings.tomodd_base_url)}


@app.get("/livez", tags=["健康检查"])
def livez() -> dict[str, str]:
    """只表示进程仍能处理请求，不代表已完成迁移或可以接收流量。"""
    return {"status": "ok"}


@app.get("/readyz", tags=["健康检查"])
def readyz() -> JSONResponse:
    """检查安全配置、数据库连接和迁移头，失败时不泄露连接或密钥细节。"""
    settings = get_settings()
    checks: dict[str, str] = {"config": "ok", "database": "failed", "migration": "failed"}
    try:
        settings.validate_runtime()
    except Exception:
        checks["config"] = "failed"
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
            checks["database"] = "ok"
            version = session.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar_one_or_none()
            if version == "a6b7c8d9e0f1":
                checks["migration"] = "ok"
    except Exception:
        checks["database"] = "failed"
        checks["migration"] = "failed"
    ready = all(value == "ok" for value in checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )


app.include_router(auth.router)
app.include_router(public.router)
app.include_router(portal.router)
app.include_router(admin.router)
app.include_router(files.router)
app.include_router(gateway.router)
