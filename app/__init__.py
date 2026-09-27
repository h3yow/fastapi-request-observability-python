"""FastAPI 请求可观测性示例应用。"""

import logging

from fastapi import FastAPI

from .exception_handlers import register_exception_handlers
from .health import router as health_router
from .logging_config import configure_logging
from .middleware import ObservabilityMiddleware
from .routes import router as api_router


def create_app() -> FastAPI:
    """创建并组装 FastAPI 应用。"""
    configure_logging(logging.INFO)

    app = FastAPI()
    app.add_middleware(ObservabilityMiddleware)
    app.include_router(api_router)
    app.include_router(health_router)
    register_exception_handlers(app)
    return app
