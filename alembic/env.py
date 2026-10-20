from alembic import context
from app.core.database import get_engine
from app.models import Base

def run_migrations():
    supplied=context.config.attributes.get('connection')
    if supplied is not None:
        name=supplied.engine.url.database or ''
        if name not in {'order_demo','order_test'} and not name.startswith(('order_automation_envcheck_','order_automation_v2b_test_')):
            raise RuntimeError('禁止迁移历史库或未知数据库')
        context.configure(connection=supplied,target_metadata=Base.metadata,compare_type=True)
        with context.begin_transaction():context.run_migrations()
        return
    from app.core.config import get_settings
    if get_settings().app_env=='history':raise RuntimeError('历史数据库禁止迁移；请显式选择 demo/test')
    if context.is_offline_mode():
        from app.core.config import get_settings
        context.configure(url=get_settings().database_url, target_metadata=Base.metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
        with context.begin_transaction():
            context.run_migrations()
    else:
        with get_engine().connect() as connection:
            context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
            with context.begin_transaction():
                context.run_migrations()
run_migrations()
