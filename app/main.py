from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api.health import router
from app.core.config import get_settings
from app.core.database import get_engine
from app.api.v1 import router as v1_router
from app.api.import_batches import router as batches_router
from app.api.errors import install_error_handlers
from app.api.customers import router as customers_router
from app.api.ai_extractions import router as ai_router
from app.api.assistant import router as assistant_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings()  # Fail early for missing configuration; never print credentials.
    yield
    get_engine().dispose()

app = FastAPI(title="OrderFlow", version="1.0.0", lifespan=lifespan,
              docs_url="/docs", redoc_url="/redoc", openapi_url="/openapi.json")
app.include_router(router)
app.include_router(batches_router)
app.include_router(v1_router)
app.include_router(customers_router)
app.include_router(ai_router)
app.include_router(assistant_router)
install_error_handlers(app)

@app.middleware('http')
async def protect_history(request,call_next):
    if get_settings().app_env=='history' and request.method not in {'GET','HEAD','OPTIONS'}:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403,content={'code':'HISTORY_READ_ONLY','message':'开发历史环境只读，请启动演示或测试环境','request_id':'history-read-only'})
    return await call_next(request)
