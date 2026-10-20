"""HTTP sample acceptance. Always roll back: no permanent task or AUTO orders.

Run: python -m scripts.verify_api --output docs/reports/module_07/07_HTTP_Sample_Results.json
Requires development dependencies and local MySQL; never takes a --commit option.
"""
import argparse
import json
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.main import app
from app.core.database import get_engine, get_db
from app.models import Base

ROOT = Path(__file__).resolve().parents[1]


def snapshot(connection):
    return {name: [dict(row) for row in connection.execute(select(table).order_by(table.c.id)).mappings()]
            for name, table in Base.metadata.tables.items()}


def verify():
    report = {"transport": "FastAPI TestClient multipart / MySQL", "permanent_write": False,
              "customers": [], "orders": []}
    with get_engine().connect() as connection:
        outer = connection.begin()
        baseline = snapshot(connection)

        def isolated_db():
            with Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False) as db:
                yield db

        app.dependency_overrides[get_db] = isolated_db
        try:
            with TestClient(app) as client:
                for cid in "ABC":
                    path = next((ROOT / "sample_files").glob(cid + "*.xlsx"))
                    response = client.post("/api/v1/imports", data={"customer_id": cid},
                                           files={"file": (path.name, path.read_bytes())},
                                           headers={"Idempotency-Key": "M07-rollback-acceptance-" + cid})
                    response.raise_for_status()
                    task = response.json()["data"]
                    task_id = task["task_id"]
                    queried = client.get(f"/api/v1/imports/{task_id}")
                    queried.raise_for_status()
                    rows = client.get(f"/api/v1/imports/{task_id}/rows", params={"page_size": 100})
                    rows.raise_for_status()
                    report["customers"].append({"http_status": response.status_code, "task": task,
                                               "rows": rows.json()["data"]["items"]})
                    for row in rows.json()["data"]["items"]:
                        if row["created_order_id"]:
                            order = client.get(f"/api/v1/orders/{row['created_order_id']}")
                            order.raise_for_status()
                            report["orders"].append(order.json()["data"])
            report["inside_transaction_counts"] = {k: len(v) for k, v in snapshot(connection).items()}
        finally:
            app.dependency_overrides.pop(get_db, None)
            outer.rollback()
        final = snapshot(connection)
        assert final == baseline, "All five tables must be restored after acceptance"
        report["final_counts"] = {k: len(v) for k, v in final.items()}
        report["all_business_data_unchanged"] = True
    report["totals"] = {key: sum(item["task"][key] for item in report["customers"])
                        for key in ("total_rows", "success_rows", "unmatched_rows", "failed_rows")}
    return report


def main():
    parser = argparse.ArgumentParser(description="经过 HTTP 上传三家样例并始终回滚")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify()
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"totals": report["totals"], "final_counts": report["final_counts"],
                      "rolled_back": report["all_business_data_unchanged"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
