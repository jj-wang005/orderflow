"""Pytest safety boundary: application profile is test; SQL uses disposable schemas."""
import os
import re
import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

CONNECTION_AUDIT = []
BLOCKED_CONNECTIONS = []

TEMP_DATABASE = re.compile(r"order_automation_(?:v2b_test|envcheck)_[a-f0-9]{32}\Z")

def guard_connection(dialect, connection_record, args, params):
    if dialect.name != 'mysql':
        return
    database = params.get('database', params.get('db'))
    if database is not None and not TEMP_DATABASE.fullmatch(database):
        BLOCKED_CONNECTIONS.append(database)
        raise RuntimeError('Pytest forbids connections to managed/history databases; use a disposable test schema')
    CONNECTION_AUDIT.append(database or '<server-only>')

def guard_statement(conn, cursor, statement, parameters, context, executemany):
    # Server-only credentials may create/drop generated schemas, never inspect or
    # administer a managed database. Dialect initialization uses these SELECTs.
    ddl = re.match(r'(?is)^\s*(?:CREATE|DROP)\s+DATABASE\s+`?([\w]+)`?', statement)
    if ddl and not TEMP_DATABASE.fullmatch(ddl.group(1)):
        raise RuntimeError('Pytest forbids DDL outside disposable databases')
    use = re.match(r'(?is)^\s*USE\s+`?([\w]+)`?', statement)
    if use and not TEMP_DATABASE.fullmatch(use.group(1)):
        raise RuntimeError('Pytest forbids SQL targeting managed databases')
    if conn is not None and conn.engine.url.database is None:
        if not ddl and not re.match(r'(?is)^\s*(?:SELECT\s+(?:VERSION\s*\(|DATABASE\s*\(|@@|1\b)|SHOW\s+VARIABLES)', statement):
            raise RuntimeError('Server-only test connection permits temporary database DDL only')
    # Also refuse qualified cross-schema SQL from an otherwise safe connection.
    if re.search(r'(?i)(?:`?(?:order_demo|order_test|order_automation)`?\s*\.|\bUSE\s+`?(?:order_demo|order_test|order_automation)\b)', statement):
        raise RuntimeError('Pytest forbids SQL targeting managed databases')

def pytest_configure(config):
    if os.environ.get('APP_ENV', 'test') != 'test':
        raise pytest.UsageError('Pytest requires APP_ENV=test')
    if os.environ.get('DB_NAME', 'order_test') != 'order_test':
        raise pytest.UsageError('Pytest requires DB_NAME=order_test')
    os.environ['APP_ENV'] = 'test'
    os.environ['DB_NAME'] = 'order_test'
    event.listen(Engine, 'do_connect', guard_connection)
    event.listen(Engine, 'before_cursor_execute', guard_statement)

def pytest_unconfigure(config):
    import json
    from pathlib import Path
    audit = os.environ.get('PYTEST_DB_AUDIT')
    if audit:
        target = Path(audit).resolve()
        root = Path(__file__).resolve().parents[1]
        if root in target.parents:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({'connections':CONNECTION_AUDIT, 'blocked':BLOCKED_CONNECTIONS}, indent=2), encoding='utf-8')
    for name, handler in [('do_connect', guard_connection), ('before_cursor_execute', guard_statement)]:
        if event.contains(Engine, name, handler):
            event.remove(Engine, name, handler)

def pytest_sessionstart(session):
    if os.getenv('RUN_DB_TESTS') == '1':
        from app.core.config import get_settings
        settings = get_settings()
        if settings.app_env != 'test' or settings.db_name != 'order_test':
            raise pytest.UsageError('DB tests require test/order_test settings')

from tests.shared_fixtures import legacy_runtime, legacy_engine
