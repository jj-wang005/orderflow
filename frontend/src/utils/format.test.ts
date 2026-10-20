import { describe, it, expect } from 'vitest'
import { decimal, money, price, positiveId, dateTime } from './format'
import { statusLabel, sourceLabel } from './status'

describe('精确展示与中文状态', () => {
  it.each([
    ['1234.5600', '1,234.56'], ['999999999999999.999', '999,999,999,999,999.999'],
    ['-12345678901234567.0001', '-12,345,678,901,234,567.0001'], ['0.001', '0.001'], ['12.000', '12'],
  ])('Decimal %s 不转换成浮点', (input, expected) => expect(decimal(input)).toBe(expected))
  it('金额不重新计算、舍入或丢失超出两位的小数', () => {
    expect(money('999999999999999999.99')).toBe('999,999,999,999,999,999.99')
    expect(money('1.005')).toBe('1.005'); expect(money('75.00')).toBe('75.00')
    expect(price('12.7000')).toBe('12.7000'); expect(decimal(null)).toBe('—')
  })
  it.each(['0','-1','1.1','abc','9007199254740992'])('拒绝非法或不安全 ID %s', input => expect(positiveId(input)).toBeNull())
  it('中文状态区分匹配与建单，参考异常优先使用后端中文说明', () => {
    expect(statusLabel('MATCHED')).not.toBe(statusLabel('SUCCESS'))
    expect(statusLabel('INVALID_DATA', '', 'INVALID_REFERENCE')).toBe('历史参考无效')
    expect(statusLabel('SUCCESS','建单成功')).toBe('建单成功')
    expect(sourceLabel('SEED')).toBe('模拟历史参考'); expect(sourceLabel('AUTO')).toBe('历史匹配建单')
    expect(sourceLabel('AUTO',true)).toBe('人工补全建单')
    expect(statusLabel('REVIEW_REQUIRED','采购唯一性待人工核实')).toBe('待确认是否重复采购')
    expect(dateTime('2026-10-13T13:21:11')).toBe('2026-10-13 13:21:11')
    expect(dateTime('2026-10-13T08:21:49.300Z')).toBe('2026-10-13 16:21:49')
  })
})
