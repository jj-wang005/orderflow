"""Deliberately invalid references: call ONLY inside an outer rollback transaction."""
import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from sqlalchemy import select
from app.models import SalesOrder

FAULTS=json.loads((Path(__file__).parent/'fixtures/invalid_reference_prices.json').read_text(encoding='utf-8'))

def install_reference_faults(connection):
    assert connection.in_transaction(), 'Rollback transaction required'
    for spec in FAULTS:
        values={k:v for k,v in spec.items() if k!='purpose'}
        for k in ('quantity','unit_price'):values[k]=Decimal(values[k])
        values['created_at']=datetime.fromisoformat(values['created_at']);values['updated_at']=values['created_at']
        values['amount']=(values['unit_price']*values['quantity']).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        old=connection.execute(select(SalesOrder.__table__).where(SalesOrder.order_no==values['order_no'])).mappings().first()
        if old:
            assert old['source_type']=='SEED' and old['source_order_id'] is None and old['import_task_id'] is None
            connection.execute(SalesOrder.__table__.update().where(SalesOrder.id==old['id']).values(**values))
        else:connection.execute(SalesOrder.__table__.insert().values(**values))
