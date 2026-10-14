import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { BudgetBar } from './BudgetBar'
import { formatRemainingUsd } from '@/lib/campaigns'

describe('BudgetBar render', () => {
  it('shows remaining/original, the bar and the spent label', () => {
    const html = renderToStaticMarkup(<BudgetBar remaining={3850} original={5000} spent={23} />)
    expect(html).toContain(`${formatRemainingUsd(3850)} / ${formatRemainingUsd(5000)}`)
    expect(html).toContain('23% gastado')
    expect(html).toContain('width:23%')
  })

  it('derives the pct when the sync did not send spent_pct', () => {
    const html = renderToStaticMarkup(<BudgetBar remaining={2500} original={5000} />)
    expect(html).toContain(`${formatRemainingUsd(2500)} / ${formatRemainingUsd(5000)}`)
    expect(html).toContain('50% gastado')
  })

  it('hides the bar when a pct cannot be computed', () => {
    const html = renderToStaticMarkup(<BudgetBar remaining={1000} original={null} spent={null} />)
    expect(html).toContain(formatRemainingUsd(1000))
    expect(html).not.toContain('gastado')
    expect(html).not.toContain('width:')
  })

  it('flags nearly exhausted pools in destructive tone', () => {
    const html = renderToStaticMarkup(<BudgetBar remaining={200} original={5000} spent={96} />)
    expect(html).toContain('bg-destructive')
  })

  it('falls back to the dash when there is no data at all', () => {
    const html = renderToStaticMarkup(<BudgetBar remaining={null} original={null} spent={null} />)
    expect(html).toContain('—')
    expect(html).not.toContain('gastado')
  })
})