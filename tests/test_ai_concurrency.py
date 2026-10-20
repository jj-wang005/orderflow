"""Actual commit races in disposable MySQL schema only."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
from sqlalchemy import select,func
from app.models import ImportTask,SalesOrder
from app.main import app
from app.services.ai_extraction import get_extraction_service,AIExtractionService,MockAdapter
from tests.concurrency_fixtures import isolated_engine,client
from tests.test_ai_drafts import create,edited,confirm

def race(c,drafts,key):
    barrier=Barrier(2)
    def submit(draft):barrier.wait(timeout=10);return confirm(c,draft,key or uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(submit,drafts))

def test_same_draft_concurrent_confirmation_one_task_one_order(client):
    c,engine=client
    app.dependency_overrides[get_extraction_service]=lambda:AIExtractionService(MockAdapter())
    try:
        d=edited(c,create(c));results=race(c,[d,d],uuid4())
        assert [r.status_code for r in results]==[200,200]
        assert sorted(r.json()['data']['replayed'] for r in results)==[False,True]
        with engine.connect() as db:
            assert db.scalar(select(func.count()).select_from(ImportTask))==1
            assert db.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.source_type=='AUTO'))==1
    finally:app.dependency_overrides.pop(get_extraction_service,None)

def test_different_drafts_same_source_concurrent_confirmation_only_one(client):
    c,engine=client
    app.dependency_overrides[get_extraction_service]=lambda:AIExtractionService(MockAdapter())
    try:
        drafts=[edited(c,create(c)),edited(c,create(c))];results=race(c,drafts,None)
        assert sorted(r.status_code for r in results)==[200,409]
        with engine.connect() as db:
            assert db.scalar(select(func.count()).select_from(ImportTask))==1
            assert db.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.source_type=='AUTO'))==1
    finally:app.dependency_overrides.pop(get_extraction_service,None)
