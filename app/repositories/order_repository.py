"""Module 06 write primitives. Public workflow must use import_service row locks."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import SalesOrder
from app.repositories.order_matching import REFERENCE_COLUMNS

def lock_reference(session: Session, order_id: int) -> dict | None:
    row = session.execute(select(*REFERENCE_COLUMNS).where(SalesOrder.id == order_id).with_for_update()).mappings().first()
    return dict(row) if row is not None else None

def insert_order(session: Session, order: SalesOrder) -> SalesOrder:
    from app.services.purchase_guard import assign_claim
    assign_claim(session, order)
    session.add(order)
    session.flush()
    return order
