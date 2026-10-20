"""Thin synchronous HTTP facade for the module 06 transaction workflow."""
from pathlib import PureWindowsPath
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, File, Form, Header, Query, UploadFile, Response, Path
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.core.database import get_db
from app.services.excel_parser import ParseLimits
from app.services.http_import import submit_import
from app.services.import_service import ImportRejected
from app.repositories import api_queries
from app.schemas.api import Envelope, ErrorResponse, TaskSummary, Page, RowDetail, OrderDetail
from app.api.errors import APIError
from app.schemas.manual_correction import CorrectionRequest, CorrectionContext, CorrectionResult
from app.services.manual_correction import correction_context, correct_row
from app.schemas.order_operations import ManualOrderRequest, EditOrderRequest, OrderOperationResult, OrderEditContext
from app.services.order_operations import manual_create, edit_order, edit_context
from app.schemas.order_status import StatusRequest, OrderStatus,VoidRequest
from app.services.order_status import update_status,void_order
from datetime import date
from app.services.order_exports import excel_orders,pdf_order
ListStatus=Literal['CREATED','VALID','PROCESSING','SHIPPED','COMPLETED','INVALID','VOIDED']

ERRORS = {code: {"model": ErrorResponse, "description": description} for code, description in {
    400: "Excel 为空、损坏、格式不支持、工作表或表头错误", 404: "任务或销售订单不存在",
    409: "幂等键冲突或原任务尚未完成", 413: "文件超过配置大小限制", 422: "参数或客户配置不合法",
    500: "内部异常；处理期回滚，提交后响应失败需原键重试；不包含 SQL 或密码"}.items()}
router = APIRouter(prefix="/api/v1", responses=ERRORS)
DB = Annotated[Session, Depends(get_db)]
Customer = str
PageNumber = Annotated[int, Query(ge=1, description="页码，从 1 开始")]
PageSize = Annotated[int, Query(ge=1, le=100, description="每页条数，最多 100")]
TaskID = Annotated[int, Path(gt=0, description="导入接口返回的任务 ID")]


@router.post("/imports", response_model=Envelope[TaskSummary], status_code=201, tags=["导入任务"],
             summary="上传 Excel 并自动创建销售订单",
             description="同步调用模块 06。正式执行会提交 MySQL 数据，逐行业务失败仍返回任务统计。"
             "仅适用于受控本地演示，尚无鉴权。customer_id=已配置客户标识；file 选择对应 XLSX。"
             "建议 Idempotency-Key=demo-A-001：相同键、客户和文件字节重试返回原任务（200）；"
             "新任务（含文件级失败记录）返回 201。相同内容需 duplicate_confirmed=true。网络重试保留原键；新任务仍执行跨任务采购防重。"
             "当前产品和单价来自模拟 SEED，未经过真实 OMS/ERP 验证。",
             responses={200: {"model": Envelope[TaskSummary], "description": "返回原任务，不再次建单"},
                        201: {"description": "新任务已提交", "content": {"application/json": {"example": {
                            "code": "OK", "message": "处理完成", "data": {
                                "task_id": 101, "customer_id": "A", "status": "PARTIAL_SUCCESS",
                                "status_description": "部分建单成功", "total_rows": 9,
                                "success_rows": 3, "unmatched_rows": 4, "failed_rows": 2}}}}}})
def upload_import(response: Response, session: DB,
                  customer_id: Annotated[Customer, Form(description="已保存正式配置的客户 ID", examples=["A"])],
                  file: Annotated[UploadFile, File(description="客户对应的 .xlsx 文件，默认最大 10 MiB")],
                  idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", min_length=1,
                     max_length=128, pattern=r"^[!-~]+$", description="可选，1～128 个可见 ASCII 字符；示例 demo-A-001；区分大小写")] = None,
                  duplicate_confirmed: Annotated[bool, Form()] = False,
                  retry_of_task_id: Annotated[int | None, Form(gt=0)] = None):
    try:
        max_bytes = get_settings().upload_max_bytes
        # Read only one bounded buffer; no server file path is built from filename.
        content = file.file.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise APIError(413, "FILE_TOO_LARGE", "文件超过允许的上传大小限制")
        name = PureWindowsPath(file.filename or "").name
        if not name or len(name) > 255 or any(ord(c) < 32 for c in name):
            raise APIError(400, "INVALID_FILE", "文件名缺失、过长或包含非法字符")
        if PureWindowsPath(name).suffix.lower() != ".xlsx":
            raise APIError(400, "INVALID_FILE", "仅支持 .xlsx 文件")
        with session.begin():
            result = submit_import(session, customer_id, content, name, idempotency_key,
                                   ParseLimits(max_size_bytes=max_bytes), duplicate_confirmed=duplicate_confirmed, retry_of_task_id=retry_of_task_id)
        if result.idempotency_replayed:
            response.status_code = 200
        return Envelope(data=result, message="返回原任务，未重复建单" if result.idempotency_replayed else "处理完成")
    except ImportRejected as exc:
        raise APIError(400, exc.code, "文件或客户配置无法创建导入任务") from None
    finally:
        file.file.close()


@router.get("/imports/{task_id}", response_model=Envelope[TaskSummary], tags=["导入任务"], summary="查询导入任务统计")
def task_detail(task_id: TaskID, session: DB):
    return Envelope(data=api_queries.get_task(session, task_id))


@router.get("/imports/{task_id}/rows", response_model=Envelope[Page[RowDetail]], tags=["导入任务"],
            summary="分页查询采购行处理结果", description="按工作表、Excel 行号、ID 稳定排序。normalized_data 包含标准化客户字段和本次 quantity；无效行保留 raw_data。SUCCESS 表示新订单已创建，MATCHED 仅表示历史匹配成功。")
def task_rows(task_id: TaskID, session: DB, page: PageNumber = 1, page_size: PageSize = 20):
    return Envelope(data=api_queries.get_rows(session, task_id, page, page_size))


@router.get("/orders", response_model=Envelope[Page[OrderDetail]], tags=["销售订单"], summary="分页查询销售订单与模拟历史订单")
def orders(session: DB, page: PageNumber = 1, page_size: PageSize = 20,
           customer_id: Annotated[Customer | None, Query(description="按客户筛选")] = None,
           source_type: Annotated[Literal["SEED", "AUTO"] | None, Query(description="订单来源类型")] = None,
           status: Annotated[ListStatus | None, Query(description="默认隐藏已作废，可指定VOIDED")] = None,
           date_from:date|None=None,date_to:date|None=None):
    return Envelope(data=api_queries.get_orders(session, page, page_size, customer_id, source_type,status,date_from,date_to))

@router.get('/orders/export.xlsx',tags=['销售订单'],summary='导出当前筛选的全部订单，最多10000条')
def export_orders(session:DB,customer_id:str|None=None,source_type:Literal['SEED','AUTO']|None=None,status:ListStatus|None=None,date_from:date|None=None,date_to:date|None=None):
    content=excel_orders(session,customer_id=customer_id,source_type=source_type,status=status,date_from=date_from,date_to=date_to)
    return Response(content,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="sales-orders.xlsx"','Cache-Control':'no-store'})

@router.get('/orders/{order_id}/report.pdf',tags=['销售订单'],summary='内部销售订单详情PDF')
def export_order_pdf(order_id:Annotated[int,Path(gt=0)],session:DB):
    return Response(pdf_order(session,order_id),media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="sales-order-{order_id}.pdf"','Cache-Control':'no-store'})

@router.post('/orders/{order_id}/void',response_model=Envelope[OrderOperationResult],tags=['销售订单'],summary='已创建订单不可恢复作废，保留来源占用')
def post_void_order(order_id:Annotated[int,Path(gt=0)],body:VoidRequest,session:DB):
    with session.begin():result=void_order(session,order_id,body)
    return Envelope(data=result,message='返回原作废记录' if result['replayed'] else '订单已作废，不可恢复')

@router.get('/imports/{task_id}/rows/{row_id}/match-explanation',tags=['人工补录'],summary='只读查看任务规则与当前可验证匹配结果')
def match_explanation(task_id:TaskID,row_id:Annotated[int,Path(gt=0)],session:DB):
    from app.services.manual_correction import _row,_config
    from app.models import ImportTask
    from app.schemas.purchase_row import NormalizedPurchaseRow
    from app.services.matching_engine import match_purchase_row
    task=session.get(ImportTask,task_id)
    if task is None:raise APIError(404,'TASK_NOT_FOUND','任务不存在')
    row=_row(session,task_id,row_id);config=_config(session,task)
    import json
    result=match_purchase_row(session,NormalizedPurchaseRow.model_validate_json(json.dumps(row.normalized_data)),config=config) if row.normalized_data else None
    effective_fields=list(dict.fromkeys([*config.rule.match_fields,*config.rule.validation_config.consistency_fields]))
    return Envelope(data={'customer_id':task.customer_id,'config_version':task.config_version,'rule_version':task.rule_version,
        'match_fields':effective_fields,'configured_match_fields':config.rule.match_fields,'consistency_fields':config.rule.validation_config.consistency_fields,
        'match_values':{f:(row.normalized_data or {}).get(f) for f in effective_fields},
        'historical_quantity':config.rule.filter_conditions.historical_quantity,'saved_row_status':row.status,'saved_error':row.error_message,
        'current_result':result.model_dump(mode='json') if result else None,
        'safety':'同客户；合格历史状态；唯一产品；有效正单价；created_at DESC,id DESC。当前只读检查不改写原处理结果。'})


@router.get("/orders/{order_id}", response_model=Envelope[OrderDetail], tags=["销售订单"],
            summary="查询销售订单详情及来源", description="用明细中的 created_order_id 核实新订单；source_order_id 追溯历史来源。金额已按本次采购数量计算。")
def order_detail(order_id: Annotated[int, Path(gt=0, description="销售订单 ID")], session: DB):
    return Envelope(data=api_queries.get_order(session, order_id))


@router.get("/imports/{task_id}/rows/{row_id}/correction", response_model=Envelope[CorrectionContext], tags=["人工补录"],
            summary="查看采购明细补录字段与修改历史",
            description="按客户真实配置带入原始字段；原始 Excel 不覆盖。最多返回最近 100 次历史。")
def get_correction(task_id: TaskID, row_id: Annotated[int, Path(gt=0)], session: DB):
    return Envelope(data=correction_context(session, task_id, row_id))


@router.post("/imports/{task_id}/rows/{row_id}/correction", response_model=Envelope[CorrectionResult], tags=["人工补录"],
             summary="补录采购字段、重新校验匹配并尝试建单",
             description="正式提交会写入 MySQL。只接收客户采购字段，不接受产品或价格。"
             "request_key 为操作 UUID，重试沿用；expected_revision 防止覆盖其他修改。"
             "成功行禁止再建单；业务失败仍保存修改与原因。无鉴权，仅用于受控本地环境。")
def post_correction(task_id: TaskID, row_id: Annotated[int, Path(gt=0)],
                    body: CorrectionRequest, session: DB):
    with session.begin():
        result = correct_row(session, task_id, row_id, body)
    return Envelope(data=result, message="返回原补录操作，未重复处理" if result["replayed"] else "补录已处理，请核对明细结果")


@router.post("/imports/{task_id}/rows/{row_id}/manual-order", response_model=Envelope[OrderOperationResult], tags=["人工建单"], summary="未匹配采购行手动新增销售订单",
             description="仅 NOT_MATCHED 且复核仍无历史参考的行；正式提交写入 MySQL。核对依据选填；金额后端计算。原 UUID 重试，不新增第二单。服务保留来源标记，未接真实 OMS；无鉴权，仅限受控演示。")
def post_manual_order(task_id: TaskID, row_id: Annotated[int, Path(gt=0)], body: ManualOrderRequest, session: DB):
    with session.begin():result=manual_create(session,task_id,row_id,body)
    return Envelope(data=result,message="返回原操作" if result['replayed'] else "手动建单成功")


@router.get("/orders/{order_id}/edit", response_model=Envelope[OrderEditContext], tags=["销售订单"],summary="查询可编辑订单与修改历史")
def get_order_edit(order_id: Annotated[int, Path(gt=0)], session: DB):
    return Envelope(data=edit_context(session,order_id))


@router.patch("/orders/{order_id}", response_model=Envelope[OrderOperationResult], tags=["销售订单"],summary="编辑 AUTO CREATED 订单",
              description="仅未确认 AUTO；后端强制状态、来源、版本及历史匹配保护。产品只读；改价须提供人工核对依据，不代表 OMS/ERP 验证。金额后端重算，不修改来源字段，修改记录与订单同事务。无鉴权，仅限受控演示。")
def patch_order(order_id: Annotated[int, Path(gt=0)], body: EditOrderRequest, session: DB):
    with session.begin():result=edit_order(session,order_id,body)
    return Envelope(data=result,message="返回原操作" if result['replayed'] else "订单编辑成功，金额已重新计算")


@router.patch('/orders/{order_id}/status',response_model=Envelope[OrderOperationResult],tags=['销售订单'],summary='保存订单履约状态',
              description='仅管理关联成功采购明细的 AUTO 订单，SEED 只读。按已创建→已确认→进行中→已发货→已完成顺序流转。'
              '重复状态不重复记日志；版本防覆盖，原UUID重试。404 不存在，409 版本/流转/关联冲突，422 参数或订单不合法。'
              '正式保存写入数据库；当前无鉴权，仅适合受控本地环境。')
def patch_order_status(order_id: Annotated[int,Path(gt=0)],body:StatusRequest,session:DB):
    with session.begin():result=update_status(session,order_id,body)
    return Envelope(data=result,message='状态已保存' if not result['replayed'] else '当前状态已确认，未重复更新')
