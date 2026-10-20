import logging
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.config import get_settings

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/health", tags=["Health"])
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.error("Database health check failed")
        return JSONResponse(status_code=503, content={"status": "unhealthy", "database": "unavailable"})
    s=get_settings()
    return {"status": "ok", "database": "ok",'environment':s.app_env,'database_name':s.db_name,
            'environment_label':{'history':'开发历史（只读）','demo':'演示环境','test':'测试环境'}[s.app_env]}
