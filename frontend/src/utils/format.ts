/** Display Decimal strings without converting business values to floating point. */
export function decimal(value: string | null | undefined, minScale = 0): string {
  if (value == null || value === '') return '—'
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value)
  if (!match) return value
  const [, sign, whole = '', fraction = ''] = match
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  const digits = fraction.replace(/0+$/, '').padEnd(minScale, '0')
  return sign + grouped + (digits ? '.' + digits : '')
}
export const money = (value: string | null | undefined) => decimal(value, 2)
export const price = (value: string | null | undefined) => decimal(value, 4)
export function dateTime(value: string | null | undefined): string {
  if (!value) return '—'
  if (/Z$|[+-]\d{2}:\d{2}$/.test(value)) {
    const date = new Date(value)
    if (!Number.isNaN(date.getTime())) {
      const parts = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).formatToParts(date)
      const part = (type: Intl.DateTimeFormatPartTypes) => parts.find(p => p.type === type)?.value || ''
      return `${part('year')}-${part('month')}-${part('day')} ${part('hour')}:${part('minute')}:${part('second')}`
    }
  }
  return value.replace('T', ' ').replace(/\.\d+$/, '')
}
export function fileSize(bytes: number) { return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB` }
export function valueText(value: unknown): string {
  return value == null || value === '' ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value)
}
export function positiveId(value: unknown): number | null {
  const text = String(value ?? '')
  if (!/^[1-9]\d*$/.test(text)) return null
  const n = Number(text); return Number.isSafeInteger(n) ? n : null
}
