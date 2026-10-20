"""Real HTTP multipart + MySQL. Default tests roll back; concurrency commits
only uniquely keyed test tasks and deletes exactly those tasks/orders in finally.
"""
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from threading import Barrier, Lock
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func, delete, event
from sqlalchemy.orm import Session
from app.main import app
from app.core.database import get_engine, get_db, get_session_factory
from app.core.config import get_settings
from app.models import Base, SalesOrder, CustomerConfig, MatchRule, ImportTask, ImportTaskRow
from app.services import http_import
from app.services.matching_engine import match_purchase_row
from app.schemas.purchase_row import NormalizedPurchaseRow
from tests.test_excel_parser import workbook_bytes
from tests.reference_faults import install_reference_faults

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "config/historical_seed_v1.json").read_text(encoding="utf-8"))
pytestmark = [pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="Disposable MySQL only"), pytest.mark.usefixtures("legacy_runtime")]


def snapshot(connection):
    return {name: [tuple(row) for row in connection.execute(select(table).order_by(table.c.id))]
            for name, table in Base.metadata.tables.items()}


def upload(client, cid="A", content=None, key=None, filename="test.xlsx", *, duplicate_confirmed=False):
    if content is None:
        content = next((ROOT / "sample_files").glob(cid + "*.xlsx")).read_bytes()
    return client.post("/api/v1/imports", data={"customer_id": cid, "duplicate_confirmed": str(duplicate_confirmed).lower()},
                       files={"file": (filename, content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                       headers={"Idempotency-Key": key} if key is not None else {})


from tests.shared_fixtures import legacy_api as api


def data(response, status=200):
    assert response.status_code == status, response.text
    return response.json()["data"]


def assert_sample(client, connection, cid):
    task = data(upload(client, cid), 201)
    assert data(client.get(f"/api/v1/imports/{task['task_id']}"))["total_rows"] == task["total_rows"]
    rows = data(client.get(f"/api/v1/imports/{task['task_id']}/rows", params={"page_size": 100}))["items"]
    expected = [e for e in MANIFEST["purchase_expectations"] if e["customer_id"] == cid]
    assert len(rows) == len(expected)
    for row, spec in zip(rows, expected):
        assert row["row_number"] == spec["row_number"]
        expected_status = {"MATCHED": "SUCCESS", "INVALID_REFERENCE": "INVALID_DATA", "AMBIGUOUS_MATCH": "AMBIGUOUS"}.get(spec["expected_status"], spec["expected_status"])
        assert row["status"] == expected_status
        assert row["status_description"] and any('\u4e00' <= c <= '\u9fff' for c in row["status_description"])
        if expected_status == "SUCCESS":
            order = data(client.get(f"/api/v1/orders/{row['created_order_id']}"))
            reference = connection.execute(select(SalesOrder.__table__).where(SalesOrder.id == row["matched_order_id"])).mappings().one()
            assert reference["order_no"] == spec["expected_order_no"]
            assert reference["source_type"] != "AUTO" and reference["status"] == "VALID"
            assert order["source_order_id"] == reference["id"]
            assert order["import_task_id"] == task["task_id"]
            assert order["product_code"] == reference["product_code"]
            assert Decimal(order["unit_price"]) == reference["unit_price"]
            assert Decimal(order["quantity"]) == Decimal(spec["values"]["quantity"])
            assert Decimal(order["amount"]) == (reference["unit_price"] * Decimal(order["quantity"])).quantize(Decimal("0.01"))
            assert order["is_simulated_reference"] and "模拟" in order["price_description"]
            assert row["status_description"] == "建单成功"
        else:
            assert row["created_order_id"] is None and row["error_code"]
    assert task["success_rows"] == sum(r["status"] == "SUCCESS" for r in rows)
    assert task["unmatched_rows"] == sum(r["status"] == "NOT_MATCHED" for r in rows)
    assert task["failed_rows"] == sum(r["status"] in {"AMBIGUOUS", "INVALID_DATA", "FAILED"} for r in rows)
    return task, rows


@pytest.mark.parametrize("cid", ["A", "B", "C"])
def test_real_customer_http_upload(api, cid):
    assert_sample(*api, cid)


def test_all_22_rows_order_links_and_confirmed_auto_admission(api):
    client, connection = api
    baseline_seed = connection.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.source_type == "SEED"))
    tasks, all_rows = [], []
    for cid in "ABC":
        task, rows = assert_sample(client, connection, cid)
        tasks.append(task)
        all_rows.extend(rows)
    assert [sum(t[k] for t in tasks) for k in ("total_rows", "success_rows", "unmatched_rows", "failed_rows")] == [22, 8, 9, 5]
    assert connection.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.import_task_id.in_([t["task_id"] for t in tasks]))) == 8
    # Public policy now admits confirmed AUTO. No bulk confirmation outside rollback.
    connection.execute(SalesOrder.__table__.update().where(SalesOrder.source_type == "AUTO").values(status="VALID"))
    with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
        for row in all_rows:
            if row["status"] == "SUCCESS":
                norm = NormalizedPurchaseRow.model_validate_json(json.dumps(row["normalized_data"]))
                match = match_purchase_row(db, norm)
                assert match.status == "MATCHED"
                assert match.matched_order.status == 'VALID'
                expected=connection.execute(select(SalesOrder.__table__).where(SalesOrder.id==row['matched_order_id'])).mappings().one()
                assert match.matched_order.product_code==expected['product_code']
                if row['normalized_data']['customer_id']!='C' or Decimal(row['normalized_data']['quantity'])>500:
                    assert match.matched_order.source_type=='AUTO'
    assert connection.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.source_type == "SEED")) == baseline_seed


@pytest.mark.parametrize("path", ["/api/v1/imports/999999999", "/api/v1/imports/999999999/rows", "/api/v1/orders/999999999"])
def test_missing_resources(api, path):
    response = api[0].get(path)
    assert response.status_code == 404 and response.json()["request_id"]


@pytest.mark.parametrize("content,filename,code", [(b"", "a.xlsx", "INVALID_FILE"), (b"text", "a.csv", "INVALID_FILE"), (b"broken", "a.xlsx", "INVALID_FILE"), (b"PK\x03\x04broken", "a.xlsx", "INVALID_FILE")])
def test_invalid_files_preserve_only_current_failure_receipts(api, content, filename, code):
    client, connection = api
    before = snapshot(connection)
    response = upload(client, content=content, filename=filename)
    if filename.endswith('.csv'):
        assert response.status_code == 400 and response.json()['code'] == code
        assert snapshot(connection) == before
    else:
        # Current HTTP contract retains file-level failure receipts (no fake rows/orders).
        task = data(response, 201)
        assert task['status'] == 'FAILED' and task['total_rows'] == 0
        assert task['error_message'].startswith(code + ':')
        assert data(client.get(f"/api/v1/imports/{task['task_id']}/rows"))['total'] == 0
        after = snapshot(connection)
        assert len(after['import_task']) == len(before['import_task']) + 1
        assert {k:v for k,v in after.items() if k!='import_task'} == {k:v for k,v in before.items() if k!='import_task'}


@pytest.mark.parametrize("content", [workbook_bytes(sheet="Wrong"), workbook_bytes(headers=["错误列"])])
def test_wrong_sheet_and_header(api, content):
    task = data(upload(api[0], content=content), 201)
    assert task['status'] == 'FAILED' and task['total_rows'] == 0
    assert task['error_message'].split(':',1)[0] in {'INVALID_FILE','MISSING_COLUMN'}
    assert data(api[0].get(f"/api/v1/imports/{task['task_id']}/rows"))['total'] == 0


@pytest.mark.parametrize("cid", ["X", "", "a"])
def test_invalid_customer(api, cid):
    assert upload(api[0], cid, content=b"x").status_code == 422


@pytest.mark.parametrize("change", ["disabled_customer", "disabled_rule", "invalid_rule", "missing_customer"])
def test_unavailable_config(api, change):
    client, connection = api
    if change == "disabled_customer":
        connection.execute(CustomerConfig.__table__.update().where(CustomerConfig.customer_id == "A").values(enabled=False))
    elif change == "disabled_rule":
        connection.execute(MatchRule.__table__.update().where(MatchRule.customer_id == "A").values(enabled=False))
    elif change == "invalid_rule":
        connection.execute(MatchRule.__table__.update().where(MatchRule.customer_id == "A").values(match_fields=["unsafe_sql"]))
    else:
        # B is a valid HTTP customer identifier. Hide it only in the configuration
        # lookup; existing FK-bearing records remain untouched.
        from app.repositories.customer_configuration import ConfigurationError
        from unittest.mock import patch
        with patch.object(http_import, "load_configuration", side_effect=ConfigurationError("missing")):
            assert upload(client, "B").status_code == 422
        return
    response = upload(client)
    assert response.status_code == 422 and response.json()["code"] == "CONFIG_ERROR"


def test_file_size_configurable(api, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_max_bytes", 10)
    assert upload(api[0], content=b"x" * 11).status_code == 413


def test_filename_is_only_label(api):
    response = upload(api[0], filename=r"C:\private\..\sample.xlsx")
    assert data(response, 201)["file_name"] == "sample.xlsx"


@pytest.mark.parametrize("key", ["", "with space", "x" * 129])
def test_invalid_idempotency_header(api, key):
    assert upload(api[0], key=key).status_code == 422


def test_replay_same_key_filename_change(api):
    client, connection = api
    key = "TEST-M07-" + uuid4().hex
    first = data(upload(client, key=key), 201)
    before = snapshot(connection)
    second = data(upload(client, key=key, filename="renamed.xlsx"))
    assert second["task_id"] == first["task_id"] and second["idempotency_replayed"]
    assert snapshot(connection) == before


@pytest.mark.parametrize("change", ["customer", "bytes"])
def test_same_key_different_content_conflict(api, change):
    client, connection = api
    key = "TEST-M07-" + uuid4().hex
    data(upload(client, key=key), 201)
    before = snapshot(connection)
    response = upload(client, cid="B" if change == "customer" else "A", content=None if change == "customer" else b"different", key=key)
    assert response.status_code == 409 and response.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert snapshot(connection) == before


@pytest.mark.parametrize("key_mode", ["different", "absent", "case_sensitive"])
def test_legitimate_new_tasks(api, key_mode):
    client, connection = api
    key = "TEST-M07-" + uuid4().hex
    keys = [key, key + "-new"] if key_mode == "different" else [None, None] if key_mode == "absent" else [key, key.lower()]
    first = data(upload(client, key=keys[0]), 201)
    blocked = upload(client, key=keys[1])
    assert blocked.status_code == 409 and blocked.json()['code'] == 'DUPLICATE_FILE'
    second = data(upload(client, key=keys[1], duplicate_confirmed=True), 201)
    assert first['task_id'] != second['task_id'] and first['success_rows'] == 3
    assert second['success_rows'] == 0 and second['review_rows'] >= 3
    items = data(client.get(f"/api/v1/imports/{second['task_id']}/rows", params={'page_size':100}))['items']
    old = data(client.get(f"/api/v1/imports/{first['task_id']}/rows", params={'page_size':100}))['items']
    from uuid import uuid4 as review_key
    for original, row in zip(old, items):
        if original['status'] != 'SUCCESS': continue
        assert row['status'] == 'REVIEW_REQUIRED' and row['created_order_id'] is None
        result = data(client.post(f"/api/v1/imports/{second['task_id']}/rows/{row['id']}/purchase-review", json={
            'request_key':str(review_key()), 'expected_revision':row['manual_revision'],
            'decision':'EXISTING', 'existing_order_id':original['created_order_id'],
            'note':'测试核实：同一采购已由原任务处理'}))
        assert result['row']['status'] == 'DUPLICATE'
    assert connection.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.source_type=='AUTO')) == 3


def test_row_level_error_is_http_success(api):
    task = data(upload(api[0], content=workbook_bytes(rows=[["001", "MAT", -1], ["missing", "MAT", 12]])), 201)
    assert task["total_rows"] == 2 and task["failed_rows"] == 1 and task["unmatched_rows"] == 1


@pytest.mark.parametrize("params", [{"page": 0}, {"page_size": 101}, {"page_size": 0}])
def test_bad_pagination(api, params):
    assert api[0].get("/api/v1/orders", params=params).status_code == 422


def test_stable_pagination_and_orders_filter(api):
    client, connection = api
    before_auto_count = connection.scalar(select(func.count()).select_from(SalesOrder).where(
        SalesOrder.customer_id == "A", SalesOrder.source_type == "AUTO"))
    task = data(upload(client), 201)
    pages = [data(client.get(f"/api/v1/imports/{task['task_id']}/rows", params={"page": n, "page_size": 2})) for n in range(1, 6)]
    ids = [row["id"] for page in pages for row in page["items"]]
    assert len(ids) == len(set(ids)) == 9 and all(page["total"] == 9 for page in pages)
    assert not data(client.get(f"/api/v1/imports/{task['task_id']}/rows", params={"page": 99}))["items"]
    orders = data(client.get("/api/v1/orders", params={"customer_id": "A", "source_type": "AUTO"}))
    # Existing user imports are legitimate records, not disposable test data.
    assert orders["total"] == before_auto_count + 3
    assert all(o["source_type"] == "AUTO" and o["customer_id"] == "A" for o in orders["items"])
    created_ids = set(connection.scalars(select(SalesOrder.id).where(SalesOrder.import_task_id == task["task_id"])))
    assert len(created_ids) == 3
    assert created_ids.issubset({o["id"] for o in orders["items"]})


def test_fatal_error_rolls_back_everything_and_hides_exception(api, monkeypatch):
    client, connection = api
    before = snapshot(connection)
    original = http_import.process_import_task

    def fail_after_writes(*args):
        original(*args)
        raise RuntimeError("DB_PASSWORD=secret SELECT * FROM private C:/internal")

    monkeypatch.setattr(http_import, "process_import_task", fail_after_writes)
    response = upload(client, key="TEST-M07-" + uuid4().hex)
    assert response.status_code == 500 and response.json()["code"] == "INTERNAL_ERROR"
    assert response.headers["x-request-id"] == response.json()["request_id"]
    assert all(s not in response.text for s in ["secret", "SELECT", "C:/internal"])
    assert snapshot(connection) == before


def test_swagger_chinese_and_http_contract(api):
    client = api[0]
    assert client.get("/docs").status_code == 200
    spec = client.get("/openapi.json").json()
    operation = spec["paths"]["/api/v1/imports"]["post"]
    assert "multipart/form-data" in operation["requestBody"]["content"]
    assert "提交 MySQL" in operation["description"]
    assert any(p["name"] == "Idempotency-Key" for p in operation["parameters"])
    assert set(operation["responses"]) >= {"200", "201", "400", "409", "413", "422", "500"}


def cleanup_keys(keys):
    """Only delete orders belonging to test tasks identified by unique random keys."""
    digests = [hashlib.sha256(key.encode()).hexdigest() for key in keys]
    with get_session_factory()() as db, db.begin():
        ids = list(db.scalars(select(ImportTask.id).where(ImportTask.idempotency_key_digest.in_(digests))))
        if ids:
            db.execute(delete(ImportTaskRow).where(ImportTaskRow.task_id.in_(ids)))
            db.execute(delete(SalesOrder).where(SalesOrder.import_task_id.in_(ids), SalesOrder.source_type == "AUTO"))
            db.execute(delete(ImportTask).where(ImportTask.id.in_(ids)))


def test_two_real_concurrent_http_requests_same_key(monkeypatch):
    key = "TEST-M07-CONCURRENT-" + uuid4().hex
    engine = get_engine()
    with engine.connect() as conn:
        before = snapshot(conn)
    barrier, lock, calls = Barrier(2), Lock(), []
    first_reads = Barrier(2)
    reads_seen = []
    def after_first_key_read(conn, cursor, statement, parameters, context, executemany):
        sql = ' '.join(statement.split())
        if sql.startswith('SELECT import_task.id FROM import_task WHERE import_task.idempotency_key_digest'):
            with lock: reads_seen.append(True)
            first_reads.wait(timeout=10)
    event.listen(engine, 'after_cursor_execute', after_first_key_read)
    original_process = http_import.process_import_task

    def counted_process(*args, **kwargs):
        with lock:
            calls.append(args[1])
        return original_process(*args, **kwargs)

    monkeypatch.setattr(http_import, "process_import_task", counted_process)
    try:
        def request():
            # Race before HTTP entry, not inside the customer lock: both requests
            # must be able to reach the barrier without waiting for each other.
            barrier.wait(timeout=10)
            with TestClient(app, raise_server_exceptions=False) as client:
                return upload(client, key=key)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: request(), range(2)))
        assert sorted(r.status_code for r in results) == [200, 201], [r.text for r in results]
        assert len(reads_seen) == 2  # The controlled old-snapshot interleaving actually occurred.
        tasks = [r.json()["data"] for r in results]
        assert tasks[0]["task_id"] == tasks[1]["task_id"] and len(calls) == 1
        with engine.connect() as conn:
            task_id = tasks[0]["task_id"]
            assert conn.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.import_task_id == task_id)) == 3
            assert conn.scalar(select(func.count()).select_from(ImportTaskRow).where(ImportTaskRow.task_id == task_id)) == 9
    finally:
        event.remove(engine, 'after_cursor_execute', after_first_key_read)
        cleanup_keys([key])
        with engine.connect() as conn:
            assert snapshot(conn) == before


def test_retry_after_committed_response_failure(monkeypatch):
    """Force failure after commit, then retry from a different client/connection."""
    import fastapi.routing
    key = "TEST-M07-LOST-RESPONSE-" + uuid4().hex
    with get_engine().connect() as conn:
        before = snapshot(conn)
    original = fastapi.routing.serialize_response

    async def lose_response(*args, **kwargs):
        raise RuntimeError("simulate lost HTTP response AFTER route commit")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(fastapi.routing, "serialize_response", lose_response)
            with TestClient(app, raise_server_exceptions=False) as client:
                assert upload(client, key=key).status_code == 500
        with TestClient(app) as client:
            task = data(upload(client, key=key))
            assert task["idempotency_replayed"] and task["success_rows"] == 3
        with get_engine().connect() as conn:
            assert conn.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.import_task_id == task["task_id"])) == 3
    finally:
        cleanup_keys([key])
        with get_engine().connect() as conn:
            assert snapshot(conn) == before


def test_old_snapshot_replays_key_even_with_newer_same_file_task():
    """Real separate commits: a current read must find the key, not just latest file."""
    from app.services.excel_parser import ParseLimits
    engine = get_engine()
    keys = ['TEST-M07-OLD-'+uuid4().hex, 'TEST-M07-NEW-'+uuid4().hex]
    content = next((ROOT/'sample_files').glob('A*.xlsx')).read_bytes()
    with engine.connect() as conn: before = snapshot(conn)
    try:
        with get_session_factory()() as reader, reader.begin():
            digest = hashlib.sha256(keys[0].encode()).hexdigest()
            assert reader.scalar(select(ImportTask.id).where(ImportTask.idempotency_key_digest==digest)) is None
            with get_session_factory()() as writer, writer.begin():
                first = http_import.submit_import(writer,'A',content,'first.xlsx',keys[0],ParseLimits())
            with get_session_factory()() as writer, writer.begin():
                newer = http_import.submit_import(writer,'A',content,'newer.xlsx',keys[1],ParseLimits(),duplicate_confirmed=True)
                assert newer.success_rows==0 and newer.review_rows>=3
            replay = http_import.submit_import(reader,'A',content,'renamed.xlsx',keys[0],ParseLimits())
            assert replay.idempotency_replayed and replay.task_id==first.task_id
            assert replay.task_id != newer.task_id
        with engine.connect() as conn:
            assert conn.scalar(select(func.count()).select_from(ImportTask)) == 2
            assert conn.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.source_type=='AUTO')) == 3
    finally:
        cleanup_keys(keys)
        with engine.connect() as conn: assert snapshot(conn)==before
