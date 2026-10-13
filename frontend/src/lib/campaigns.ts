import type { CampaignListItem } from '@/api/types'
import { whopSummary } from '@/lib/whop'

/** Main list. Archived and parked live on the Archivo tab. */
export const LIVE_FILTER = 'live'
export const ARCHIVE_FILTER = 'archive'

const ARCHIVE_STATUSES = new Set(['archived', 'parked'])

export function filterCampaigns(items: CampaignListItem[], status: string, q: string): CampaignListItem[] {
  const needle = q.trim().toLowerCase()
  return items.filter((c) => {
    if (status === LIVE_FILTER && ARCHIVE_STATUSES.has(c.status)) return false
    if (status === ARCHIVE_FILTER && !ARCHIVE_STATUSES.has(c.status)) return false
    if (status !== 'all' && status !== LIVE_FILTER && status !== ARCHIVE_FILTER && c.status !== status) return false
    if (!needle) return true
    const org = whopSummary(c.source_metadata).org.toLowerCase()
    return c.name.toLowerCase().includes(needle) || org.includes(needle) || String(c.id) === needle
  })
}

export function formatRemainingUsd(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—'
  return `$${Math.round(n).toLocaleString('es-ES')}`
}
