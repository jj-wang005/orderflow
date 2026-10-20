from functools import lru_cache
from sqlalchemy import create_engine,event,text
from sqlalchemy.orm import Session, sessionmaker
from app.core.config import get_settings

@lru_cache
def get_engine():
    settings=get_settings()
    engine=create_engine(settings.database_url, pool_pre_ping=True,
                         connect_args={"connect_timeout": 5, "read_timeout": 5, "write_timeout": 5},
                         hide_parameters=True)
    @event.listens_for(engine,'begin')
    def protect_transaction(conn):
        if settings.app_env=='history':conn.exec_driver_sql('SET TRANSACTION READ ONLY')
        else:
            owner=conn.scalar(text('SELECT IS_USED_LOCK(:name)'),{'name':'cvte:maintenance:'+settings.db_name})
            if owner is not None:raise RuntimeError('该环境正在维护，拒绝新事务')
    @event.listens_for(engine,'checkout')
    def check_identity(dbapi_connection,record,proxy):
        cursor=dbapi_connection.cursor()
        try:
            cursor.execute('SELECT DATABASE()')
            if cursor.fetchone()[0]!=settings.db_name:raise RuntimeError('数据库实际连接与环境不一致')
            if settings.app_env!='history':
                cursor.execute('SELECT profile FROM environment_identity WHERE id=1')
                row=cursor.fetchone()
                if not row or row[0]!=settings.app_env:raise RuntimeError('环境身份不一致')
        finally:
            cursor.close();dbapi_connection.rollback()
    return engine

@lru_cache
def get_session_factory():
    return sessionmaker(bind=get_engine(), expire_on_commit=False)

def get_db():
    with get_session_factory()() as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
