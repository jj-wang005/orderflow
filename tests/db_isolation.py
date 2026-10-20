"""Explicit temporary MySQL schemas; no managed database connection or reset."""
from contextlib import contextmanager
from uuid import uuid4
import os
import re
from dotenv import dotenv_values
from sqlalchemy import create_engine, URL
from app.models import Base
from app.services.customer_configuration import ROOT

PREFIXES = {'order_automation_v2b_test_', 'order_automation_envcheck_'}

def temporary_name(name):
    return any(re.fullmatch(re.escape(prefix)+r'[a-f0-9]{32}', name or '') for prefix in PREFIXES)

def admin_test_engine(name=None):
    if name is not None and not temporary_name(name):
        raise RuntimeError('Only generated disposable database names are allowed')
    # Credentials are read locally; the history database is never selected/connected.
    raw = dotenv_values(ROOT/'.env')
    url = URL.create('mysql+pymysql', username=raw.get('DB_USER'), password=raw.get('DB_PASSWORD'),
                     host=raw.get('DB_HOST','127.0.0.1'), port=int(raw.get('DB_PORT','3306')),
                     database=name, query={'charset':'utf8mb4'})
    return create_engine(url, hide_parameters=True, pool_pre_ping=True,
                         connect_args={'connect_timeout':5,'read_timeout':10,'write_timeout':10})

@contextmanager
def temporary_schema_engine(prefix='order_automation_v2b_test_', *, migrate=False):
    if os.environ.get('APP_ENV') != 'test' or os.environ.get('RUN_DB_TESTS') != '1':
        raise RuntimeError('Temporary schemas require explicit test environment')
    if prefix not in PREFIXES:
        raise RuntimeError('Unknown test prefix')
    name = prefix+uuid4().hex
    if not temporary_name(name):
        raise RuntimeError('Unsafe test schema')
    admin = admin_test_engine()
    engine = None
    created = False
    try:
        with admin.connect() as conn:
            conn.exec_driver_sql('CREATE DATABASE `'+name+'` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin')
        created = True
        engine = admin_test_engine(name)
        if migrate:
            from alembic import command
            from alembic.config import Config
            config = Config(str(ROOT/'alembic.ini'))
            config.set_main_option('script_location', str(ROOT/'alembic'))
            with engine.connect() as conn:
                config.attributes['connection'] = conn
                command.upgrade(config, 'head')
                conn.commit()
        else:
            Base.metadata.create_all(engine)
        yield engine
    finally:
        if engine is not None: engine.dispose()
        if created:
            with admin.connect() as conn:
                conn.exec_driver_sql('DROP DATABASE `'+name+'`')
        admin.dispose()
