"""Shared order regressions use a complete deterministic legacy baseline."""
import pytest
from tests.shared_fixtures import legacy_engine as operation_engine, rollback_http

@pytest.fixture
def api(operation_engine):
    with rollback_http(operation_engine) as result:
        yield result
