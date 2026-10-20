"""Explicit demo only. Default rolls back. --commit intentionally persists one file."""
import argparse
import json
from pathlib import Path
from sqlalchemy.exc import SQLAlchemyError
from app.core.database import get_session_factory
from app.services.import_service import import_purchase_file, ImportRejected, FatalImportError
from app.repositories.import_repository import ImportInvariantError

def main():
    parser = argparse.ArgumentParser(description="Module 06 demo: rollback by default; --commit persists a new task and AUTO orders")
    parser.add_argument("--customer-id", required=True)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--commit", action="store_true", help="Persist task, all row results and successful AUTO orders")
    args = parser.parse_args()
    try:
        with get_session_factory()() as session:
            transaction = session.begin()
            try:
                result = import_purchase_file(session, args.customer_id, args.file)
                payload = result.model_dump(mode="json")
                if args.commit:
                    transaction.commit()
                else:
                    transaction.rollback()
            except Exception:
                if transaction.is_active:
                    transaction.rollback()
                raise
        print(json.dumps({"committed": args.commit, "result": payload}, ensure_ascii=False, indent=2))
        return 0 if result.success_rows else 1
    except ImportRejected as exc:
        print(json.dumps({"committed": False, "code": exc.code, "message": exc.message}, ensure_ascii=False))
    except (SQLAlchemyError, FatalImportError, ImportInvariantError):
        print(json.dumps({"committed": False, "code": "IMPORT_ERROR", "message": "导入事务失败，未提交；请检查数据库和任务状态"}, ensure_ascii=False))
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
