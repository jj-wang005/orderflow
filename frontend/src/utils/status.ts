const labels: Record<string, string> = {
  PROCESSING: '处理中', SUCCESS: '建单成功', PARTIAL_SUCCESS: '部分建单成功', FAILED: '处理失败',
  DUPLICATE:'已关联已有采购', REVIEW_REQUIRED:'采购待核实', PENDING: '待处理', MATCHED: '历史匹配成功', NOT_MATCHED: '未找到匹配订单',
  AMBIGUOUS: '产品歧义，需核查', AMBIGUOUS_MATCH: '产品歧义，需核查',
  INVALID_DATA: '数据异常', INVALID_REFERENCE: '历史参考无效', VALID: '有效历史订单', INVALID: '无效历史订单', CREATED: '已创建',
}
export function statusLabel(status: string, description?: string, code?: string | null) {
  if(status==='REVIEW_REQUIRED')return '待确认是否重复采购'
  if(status==='NOT_MATCHED')return '已解析 · 待人工补全'
  if(status==='VOIDED')return '已作废'
  return description || labels[code || ''] || labels[status] || status
}
export function statusTone(status: string): 'success' | 'warning' | 'danger' | 'info' | 'primary' {
  if (['SUCCESS', 'VALID', 'CREATED'].includes(status)) return 'success'
  if (['REVIEW_REQUIRED','NOT_MATCHED', 'AMBIGUOUS', 'AMBIGUOUS_MATCH', 'PARTIAL_SUCCESS'].includes(status)) return 'warning'
  if (['VOIDED','FAILED', 'INVALID_DATA', 'INVALID_REFERENCE', 'INVALID'].includes(status)) return 'danger'
  return status === 'PROCESSING' ? 'primary' : 'info'
}
export function sourceLabel(source: string,manual=false) { return manual?'人工补全建单':({ SEED: '模拟历史参考', AUTO: '历史匹配建单' } as Record<string, string>)[source] || source }
