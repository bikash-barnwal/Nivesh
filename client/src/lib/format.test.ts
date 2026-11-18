import { describe, expect, it } from 'vitest'
import { crore, directionStyle, safeUrl } from './format'

describe('news formatting', () => {
  it('maps direction to a style and falls back to neutral', () => {
    expect(directionStyle('positive')).toContain('text-good')
    expect(directionStyle('negative')).toContain('text-bad')
    expect(directionStyle('weird')).toBe(directionStyle('neutral'))
  })

  it('formats crore amounts', () => {
    expect(crore(1234.5)).toBe('₹1,234.5 cr')
    expect(crore(500)).toBe('₹500 cr')
    expect(crore(null)).toBe('—')
  })

  it('only allows http(s) links', () => {
    expect(safeUrl('https://example.com/a')).toBe('https://example.com/a')
    expect(safeUrl('javascript:alert(1)')).toBeNull()
    expect(safeUrl(null)).toBeNull()
  })
})
