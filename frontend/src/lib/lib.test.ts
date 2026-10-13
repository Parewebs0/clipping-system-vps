import { describe, expect, it } from 'vitest'
import { fmtAgo, fmtBytes, fmtDuration, fmtPct, hostOf } from './format'
import { filterCampaigns, formatRemainingUsd } from './campaigns'
import { CAMPAIGN_STATUS_ORDER, errorHint, ERROR_HINTS } from './status'
import { whopSummary } from './whop'
import type { CampaignListItem } from '@/api/types'
import openapi from '../../openapi.json'

describe('format', () => {
  it('formats bytes, durations and hosts', () => {
    expect(fmtBytes(null)).toBe('—')
    expect(fmtBytes(1536)).toBe('1.5 KB')
    expect(fmtDuration(3725)).toBe('1h 2m')
    expect(fmtDuration(42)).toBe('42s')
    expect(hostOf('https://www.youtube.com/watch?v=1')).toBe('youtube.com')
    expect(fmtAgo(new Date(Date.now() - 120_000).toISOString())).toBe('hace 2 min')
    expect(fmtPct(75)).toBe('75.0%')
    expect(fmtPct(null)).toBe('—')
  })
})

describe('status catalogue matches the backend contract', () => {
  it('covers exactly the campaign statuses published in OpenAPI', () => {
    const fromApi = (openapi as any).components.schemas.CampaignListItem.properties.status.enum as string[]
    expect([...CAMPAIGN_STATUS_ORDER].sort()).toEqual([...fromApi].sort())
    expect(fromApi).not.toContain('draft')
    expect(fromApi).not.toContain('ready')
  })
  it('has hints for every error kind the ticks write', () => {
    for (const k of ['no_materials', 'llm_timeout', 'gog', 'social_only', 'unsupported_source', 'no_videos']) {
      expect(ERROR_HINTS[k]).toBeTruthy()
    }
    expect(errorHint('weird')).toBe(ERROR_HINTS.other)
  })
})

describe('whopSummary', () => {
  it('reads top-level fields and falls back to discovered', () => {
    const w = whopSummary({
      organization_name: 'Acme',
      discovered: { platforms: ['tiktok'], cpm_usd_per_1k: 1.5, reference_materials: [{ url: 'https://x.y' }, 'https://z.w'] },
      payouts: [],
    })
    expect(w.org).toBe('Acme')
    expect(w.platforms).toEqual(['tiktok'])
    expect(w.bestCpm).toBe(1.5)
    expect(w.refs.map((r) => r.url)).toEqual(['https://x.y', 'https://z.w'])
  })
  it('uses the minimum payout rate as best CPM', () => {
    expect(whopSummary({ payouts: [{ rate_cents: 300 }, { rate_cents: 150 }] }).bestCpm).toBe(1.5)
  })
  it('prefers the refreshed target rate and reads remaining budget', () => {
    const w = whopSummary({
      economics: { remaining_usd: 9000, target_rate_usd: 2.5 },
      payouts: [{ rate_cents: 100 }],
    })
    expect(w.bestCpm).toBe(2.5)
    expect(w.remainingUsd).toBe(9000)
    expect(whopSummary({ economics: { remaining_usd: 0 } }).remainingUsd).toBe(0)
    expect(formatRemainingUsd(null)).toBe('—')
    expect(formatRemainingUsd(0)).not.toBe('—')
  })
  it('survives garbage', () => {
    expect(whopSummary(null).org).toBe('')
    expect(whopSummary({ platforms: 'nope' }).platforms).toEqual([])
  })
})

describe('filterCampaigns', () => {
  const base = { source_provider: 'whop', source_metadata: {}, spec: {}, assets_count: 0, assets_total: 0, assets_transcribed: 0, clips_total: 0, clips_approved: 0, clips_approved_qa: 0, clips_published: 0 }
  const items = [
    { ...base, id: 1, name: 'Alpha', status: 'scored', source_metadata: { organization_name: 'Org X' } },
    { ...base, id: 2, name: 'Beta', status: 'failed_resolve' },
  ] as CampaignListItem[]
  it('filters by status and text (name, org, id)', () => {
    expect(filterCampaigns(items, 'all', '').length).toBe(2)
    expect(filterCampaigns(items, 'scored', '').map((c) => c.id)).toEqual([1])
    expect(filterCampaigns(items, 'all', 'org x').map((c) => c.id)).toEqual([1])
    expect(filterCampaigns(items, 'all', '2').map((c) => c.id)).toEqual([2])
  })
  it('keeps queued and review on the main view and files archived and parked away', () => {
    const more = [
      ...items,
      { ...base, id: 3, name: 'Queue', status: 'queued' },
      { ...base, id: 4, name: 'Review', status: 'needs_review' },
      { ...base, id: 5, name: 'Old', status: 'archived' },
      { ...base, id: 6, name: 'Hold', status: 'parked' },
    ] as CampaignListItem[]
    expect(filterCampaigns(more, 'live', '').map((c) => c.id)).toEqual([1, 2, 3, 4])
    expect(filterCampaigns(more, 'archive', '').map((c) => c.id)).toEqual([5, 6])
  })
})
