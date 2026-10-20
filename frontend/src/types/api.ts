export type CustomerId = string
export type OrderStatus = 'CREATED'|'VALID'|'PROCESSING'|'SHIPPED'|'COMPLETED'
export interface StatusRequest {request_key:string;expected_revision:number;status:OrderStatus}
export type CorrectionField = 'customer_order_no' | 'customer_material_code' | 'customer_batch' | 'customer_model' | 'quantity'
export interface CorrectionRequest {
  request_key: string; expected_revision: number; values: Partial<Record<CorrectionField, string | null>>; note: string;
}
export interface CorrectionAttempt {
  id: number; revision: number; submitted_values: CorrectionRequest['values'];
  before_data: Record<string, unknown> | null; after_data: PurchaseView | null;
  result: RowDetail; note: string; actor_label: string; created_at: string;
}
export interface CorrectionContext {
  row: RowDetail; revision: number; customer_id: string; values: CorrectionRequest['values'];
  fields: {field: CorrectionField; label: string; required: boolean}[];
  editable: boolean; history: CorrectionAttempt[]; history_total: number;
}
export interface CorrectionResult { row: RowDetail; revision: number; replayed: boolean; attempt: CorrectionAttempt }
export type TaskStatus = 'PROCESSING' | 'SUCCESS' | 'PARTIAL_SUCCESS' | 'FAILED'
export type RowStatus = 'PENDING' | 'SUCCESS' | 'NOT_MATCHED' | 'AMBIGUOUS' | 'INVALID_DATA' | 'FAILED' | 'DUPLICATE' | 'REVIEW_REQUIRED'
export interface Envelope<T> { code: string; message: string; data: T }
export interface Page<T> { total: number; page: number; page_size: number; items: T[] }
export interface TaskSummary {
  task_id: number; customer_id: string; file_name: string; file_hash: string;
  status: TaskStatus; status_description: string; total_rows: number; success_rows: number;
  unmatched_rows: number; failed_rows: number; pending_rows: number;
  config_version: number | null; rule_version: number | null; started_at: string | null;
  finished_at: string | null; error_message: string | null; error_summary: string | null;
  duplicate_rows?:number; review_rows?:number; batch_id?:string|null; retry_of_task_id?:number|null; created_at?:string|null;
  idempotency_replayed: boolean;
}
export interface RawCell { coordinate?: string; [key: string]: unknown }
export interface PurchaseView {
  customer_id: string; customer_order_no: string | null; customer_material_code: string | null;
  customer_batch: string | null; customer_model: string | null; quantity: string;
  sheet_name: string; row_number: number; raw_data: Record<string, unknown>; raw_cells: Record<string, RawCell>;
}
export interface RowDetail {
  duplicate_order_id?:number|null; purchase_review?:Record<string,unknown>|null; manual_revision?:number;
  id: number; task_id: number; sheet_name: string; row_number: number;
  raw_data: Record<string, unknown>; normalized_data: PurchaseView | null;
  status: RowStatus; status_description: string; matched_order_id: number | null;
  created_order_id: number | null; error_code: string | null; error_message: string | null;
}
export interface OrderDetail {
  creation_description?:string;
  edit_revision: number; is_simulated_manual: boolean;
  id: number; order_no: string; customer_id: string; customer_order_no: string | null;
  customer_material_code: string | null; customer_batch: string | null; customer_model: string | null;
  product_code: string; product_name: string; unit_price: string; quantity: string; amount: string;
  status: string; status_description: string; source_type: string; source_order_id: number | null;
  import_task_id: number | null; source_sheet_name: string | null; source_row_number: number | null;
  created_at: string; is_simulated_reference: boolean; price_description: string;
}
export interface OrderOperationBody {
  request_key: string; expected_revision: number; product: {product_code:string;product_name:string;unit_price:string};
  note:string;verification_basis?:string|null;simulation_confirmed?:boolean|null;values?:CorrectionRequest['values'];
}
export interface OrderOperationResult {order:OrderDetail;row?:RowDetail;replayed:boolean}
export interface OrderEditContext {order:OrderDetail;editable:boolean;historical_unit_price?:string|null;history:{id:number;note:string;created_at:string;before_data:Record<string,unknown>|null;after_data:Record<string,unknown>;actor_label:string;payload:Record<string,unknown>}[]}
