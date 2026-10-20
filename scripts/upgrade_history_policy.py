"""Upgrade ONLY common admission JSON; customer keys/thresholds/versions unchanged."""
import json
from sqlalchemy import select,text
from app.core.database import get_session_factory
from app.models import MatchRule
from app.schemas.customer_configuration import FilterConditions

def upgrade_policy(session):
    if session.scalar(text('SELECT DATABASE()'))!='order_automation':
        raise RuntimeError('History policy upgrade is restricted to order_automation')
    changes=[]
    for rule in session.scalars(select(MatchRule).order_by(MatchRule.customer_id,MatchRule.id).with_for_update()):
        current=rule.filter_conditions
        upgraded=FilterConditions.model_validate(current).model_dump(mode='json')
        if current!=upgraded:
            changes.append({'id':rule.id,'customer_id':rule.customer_id,'before':current,'after':upgraded})
            rule.filter_conditions=upgraded
    session.flush()
    return changes

if __name__=='__main__':
    with get_session_factory()() as session,session.begin():result=upgrade_policy(session)
    print(json.dumps(result,ensure_ascii=False))
