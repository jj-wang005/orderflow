"""Source claims are exact evidence. Business field similarity only requests review.

All writers serialize on the customer row before task/row/order locks. Unique
order digests are the database backstop, including manual creation.
"""
import hashlib
import json
from sqlalchemy import select, or_
from app.models import CustomerConfig, ImportTask, ImportTaskRow, SalesOrder

FIELDS=("customer_order_no","customer_material_code","customer_batch","customer_model")
REVIEW_FIELDS={'A':('customer_order_no','customer_material_code'),
               'B':('customer_order_no','customer_model'),
               'C':('customer_batch','customer_material_code')}

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()

def group_key(customer_id, values):
    # Broad indexed review scope; candidates separately checks the customer's
    # business combination. Neither this scope nor its digest proves identity.
    # C's optional PO must not hide an already processed batch. A review scope
    # is not a permanent unique procurement key: verified reorders can share it.
    if customer_id == 'C' and values.get('customer_batch'):
        return digest([customer_id,"batch",values["customer_batch"]])
    if values.get("customer_order_no"):
        return digest([customer_id,"purchase_order",values["customer_order_no"]])
    if values.get("customer_batch"):
        return digest([customer_id,"batch",values["customer_batch"]])
    return None


def input_key(row):
    values=row.normalized_data or {}
    return digest({f:values.get(f) for f in (*FIELDS,"quantity")})

def source_key(task,row):
    # File bytes are evidence, never an independent procurement identity.
    # Old digests remain untouched; the namespace prevents accidental reuse.
    return digest(['task-row-v2',task.customer_id,task.id,row.id,row.sheet_name,row.row_number])

def reference_key(customer_id,reference):
    return digest([customer_id,reference])

class PurchaseBlocked(ValueError):
    def __init__(self,status,message,order_id=None,*,possible=None,reason=None):
        self.status,self.message,self.order_id=status,message,order_id
        self.risk={'reason':reason,'candidates':[candidate_snapshot(o) for o in possible]} if possible is not None else None
        super().__init__(message)

def candidate_snapshot(order):
    return {**{f:getattr(order,f) for f in ('id','customer_id','order_no','import_task_id',
            'source_sheet_name','source_row_number',*FIELDS,'status','source_type','product_code','product_name')},
            'quantity':str(order.quantity),'unit_price':str(order.unit_price),'created_at':order.created_at.isoformat()}

def same_file_candidates(session,task,row):
    return list(session.scalars(select(SalesOrder).join(ImportTask,ImportTask.id==SalesOrder.import_task_id)
        .where(SalesOrder.customer_id==task.customer_id,SalesOrder.source_type=='AUTO',
               ImportTask.customer_id==task.customer_id,ImportTask.id!=task.id,
               ImportTask.file_hash==task.file_hash,SalesOrder.source_sheet_name==row.sheet_name,
               SalesOrder.source_row_number==row.row_number).order_by(SalesOrder.id.desc()).with_for_update()))

def candidates(session,task,row):
    file_risks=same_file_candidates(session,task,row)
    group=group_key(task.customer_id,row.normalized_data or {})
    if group is None:return file_risks
    values=row.normalized_data or {}
    batch_scope=task.customer_id=='C' and bool(values.get('customer_batch'))
    current_scope=(SalesOrder.customer_order_no==values["customer_order_no"] if values.get("customer_order_no") and not batch_scope else SalesOrder.customer_batch==values.get("customer_batch"))
    indexed=list(session.scalars(select(SalesOrder).where(SalesOrder.customer_id==task.customer_id,SalesOrder.source_type=="AUTO",or_(SalesOrder.purchase_group_digest==group,current_scope)).order_by(SalesOrder.id.desc()).with_for_update()))
    # The finite pre-migration set keeps NULL keys; inspect original procurement snapshots,
    # not mutable edited order fields. Never rewrite old orders to invent business IDs.
    legacy=session.execute(select(SalesOrder,ImportTaskRow.normalized_data).outerjoin(ImportTaskRow,ImportTaskRow.created_order_id==SalesOrder.id).where(SalesOrder.customer_id==task.customer_id,SalesOrder.source_type=="AUTO",SalesOrder.purchase_group_digest.is_(None)).order_by(SalesOrder.id.desc()).with_for_update()).all()
    indexed.extend(o for o,values in legacy if group_key(o.customer_id,values or {f:getattr(o,f) for f in FIELDS})==group)
    if task.customer_id in REVIEW_FIELDS:
        # Immutable original rows also protect existing non-NULL claims if an
        # order's editable procurement fields have subsequently changed.
        first=REVIEW_FIELDS[task.customer_id][0]
        indexed.extend(session.scalars(select(SalesOrder).join(ImportTaskRow,ImportTaskRow.created_order_id==SalesOrder.id)
            .where(SalesOrder.customer_id==task.customer_id,SalesOrder.source_type=='AUTO',
                   ImportTaskRow.normalized_data[first].as_string()==values.get(first))
            .order_by(SalesOrder.id.desc()).with_for_update()))
    result=sorted({o.id:o for o in indexed}.values(),key=lambda o:o.id,reverse=True)
    material=values.get('customer_material_code')
    if task.customer_id in REVIEW_FIELDS and result:
        # Keep old digests unchanged. Narrow candidates only when different
        # business combinations are proven; original snapshots protect edits.
        originals={}
        for order_id,original in session.execute(select(ImportTaskRow.created_order_id,ImportTaskRow.normalized_data)
                .where(ImportTaskRow.created_order_id.in_([o.id for o in result]))):
            originals.setdefault(order_id,[]).append(original)
        if task.customer_id=='A' and isinstance(material,str) and material.strip():
            result=[o for o in result if _a_material_may_duplicate(o,originals.get(o.id,[]),material)]
        elif task.customer_id in {'B','C'}:
            result=[o for o in result if _combination_may_duplicate(o,originals.get(o.id,[]),values,REVIEW_FIELDS[task.customer_id])]
    return sorted({o.id:o for o in [*result,*file_risks]}.values(),key=lambda o:o.id,reverse=True)

def _combination_may_duplicate(order,originals,values,fields):
    def compatible(data):
        return any(not isinstance(data.get(f),str) or not data[f].strip() for f in fields) or all(data[f]==values.get(f) for f in fields)
    if compatible({f:getattr(order,f) for f in fields}):return True
    if originals:return any(not isinstance(v,dict) or compatible(v) for v in originals)
    return order.edit_revision!=0


def _a_material_may_duplicate(order,originals,material):
    current=order.customer_material_code
    if not isinstance(current,str) or not current.strip() or current==material:return True
    if originals:
        # Missing/invalid provenance is uncertainty, not evidence of a new line.
        return any(not isinstance(original,dict) or
                   not isinstance(original.get('customer_material_code'),str) or
                   not original['customer_material_code'].strip() or
                   original['customer_material_code']==material for original in originals)
    # Unedited legacy orders can use their stored material. Edited orders with
    # no original procurement snapshot must stay in manual review.
    return order.edit_revision!=0

def exact_existing(session,task,row):
    # Only this task position is exact. This also protects legacy NULL/v1 keys.
    query=select(SalesOrder).where(SalesOrder.customer_id==task.customer_id,SalesOrder.import_task_id==task.id,SalesOrder.source_sheet_name==row.sheet_name,SalesOrder.source_row_number==row.row_number).order_by(SalesOrder.id.desc()).with_for_update()
    return session.scalar(query)

def active_review(row):
    current=(row.purchase_review or {}).get("current")
    return current if current and current.get("input_digest")==input_key(row) else None

def compatible_fields(order,row):
    values=row.normalized_data or {}
    # Uncertain legacy fields can trigger review, but cannot prove an EXISTING
    # association. Required business keys must actually agree before selection.
    if any(not values.get(f) or values[f]!=getattr(order,f) for f in REVIEW_FIELDS.get(order.customer_id,())):
        return False
    return all(not values.get(f) or not getattr(order,f) or values[f]==getattr(order,f) for f in FIELDS)

def check_purchase(session,task,row):
    session.scalar(select(CustomerConfig.id).where(CustomerConfig.customer_id==task.customer_id).with_for_update())
    existing=exact_existing(session,task,row)
    if existing is not None:
        if existing.status=='VOIDED':raise PurchaseBlocked('DUPLICATE','原来源订单已作废，采购来源占用仍保留；禁止自动重建，请核对原订单及作废记录，确需重开应另行审批受控方案',existing.id)
        raise PurchaseBlocked("DUPLICATE","该任务采购行已经建单，已关联原订单，未再次创建",existing.id)
    review=active_review(row)
    if review and review["decision"]=="NEW":
        reference=review.get('purchase_reference')
        claim=session.scalar(select(SalesOrder).where(SalesOrder.purchase_reference_digest==reference_key(task.customer_id,reference)).with_for_update()) if reference else None
        if claim is not None:
            raise PurchaseBlocked("REVIEW_REQUIRED","该稳定采购明细编号已被使用，请核实已有订单；不能把新采购决定自动改为关联旧订单",possible=[claim],reason='REFERENCE_ALREADY_CLAIMED')
        return
    possible=candidates(session,task,row)
    values=row.normalized_data or {}
    if possible:
        raise PurchaseBlocked("REVIEW_REQUIRED","发现同客户疑似重复采购：相同文件来源或业务组合曾被处理。请确认这是新的采购／加购，还是这笔采购已经处理过；相同文件、料号、批次、数量本身不能证明重复。",possible=possible,reason='SIMILAR_PROCESSED_PURCHASE')
    if group_key(task.customer_id,values) is None:
        raise PurchaseBlocked("REVIEW_REQUIRED","缺少可靠身份依据：本次采购既无采购单号，也无可追溯批次，无法检查已处理采购范围。请补充真实说明后核实；不强制编造采购编号。",possible=[],reason='INSUFFICIENT_IDENTITY')

def assign_claim(session,order):
    if order.import_task_id is None:return
    task=session.get(ImportTask,order.import_task_id)
    row=session.scalar(select(ImportTaskRow).where(ImportTaskRow.task_id==task.id,ImportTaskRow.sheet_name==order.source_sheet_name,ImportTaskRow.row_number==order.source_row_number))
    if row is None:raise PurchaseBlocked("REVIEW_REQUIRED","缺少可核实的采购来源行")
    check_purchase(session,task,row)
    order.purchase_source_digest=source_key(task,row)
    order.purchase_group_digest=group_key(task.customer_id,row.normalized_data or {})
    review=active_review(row)
    order.purchase_reference_digest=reference_key(task.customer_id,review['purchase_reference']) if review and review['decision']=='NEW' and review.get('purchase_reference') else None
