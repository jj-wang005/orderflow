import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError
from app.main import app
from app.core.config import Settings
from app.core.database import get_db
from app.models import Base

class FakeDB:
    def __init__(self, fail=False): self.fail = fail
    def execute(self, statement):
        if self.fail: raise OperationalError("SELECT 1", {}, Exception("private credential"))

@pytest.fixture
def client(monkeypatch):
    from app.core.config import get_settings
    from app.core.database import get_engine, get_session_factory
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    monkeypatch.setenv("DB_USER", "unit_test")
    monkeypatch.setenv("DB_PASSWORD", "unit_test")
    app.dependency_overrides[get_db] = lambda: FakeDB()
    with TestClient(app) as c: yield c
    app.dependency_overrides.clear()
    from app.core.config import get_settings
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()

def test_health(client):
    body = client.get("/health").json()
    assert body == {"status":"ok", "database":"ok", "environment":"test",
                    "environment_label":"测试环境", "database_name":"order_test"}

def test_unavailable_database(client):
    app.dependency_overrides[get_db] = lambda: FakeDB(True)
    response = client.get("/health")
    assert response.status_code == 503
    assert "private credential" not in response.text

def test_swagger(client):
    assert client.get("/docs").status_code == 200
    assert "/health" in client.get("/openapi.json").json()["paths"]

def test_database_boundary():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, db_user="test", db_password="test", db_name="other_database")

def test_schema():
    assert set(Base.metadata.tables) == {"sales_order", "customer_config", "match_rule", "import_task", "import_task_row", "manual_correction", "order_change", "configuration_version", "ai_extraction", "environment_identity"}
    order = Base.metadata.tables["sales_order"]
    assert len([i for i in order.indexes if i.name.startswith("ix_sales_order_match_")]) == 3
    assert order.c.quantity.type.scale == 3
    assert order.c.unit_price.type.scale == 4
    assert order.c.amount.type.scale == 2
    assert {fk.parent.name:fk.target_fullname for fk in Base.metadata.tables["import_task_row"].foreign_keys} == {
        "task_id":"import_task.id", "matched_order_id":"sales_order.id",
        "created_order_id":"sales_order.id", "duplicate_order_id":"sales_order.id"}
