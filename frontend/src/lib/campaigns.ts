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

/** «restante / original» (ej. «$3.850 / $5.000»); solo uno si falta el otro. */
export function formatPrizePool(remaining: number | null | undefined, original: number | null | undefined): string {
  const rem = remaining != null && Number.isFinite(remaining) ? remaining : null
  const orig = original != null && Number.isFinite(original) && original > 0 ? original : null
  if (rem == null && orig == null) return '—'
  if (rem == null) return formatRemainingUsd(orig)
  if (orig == null) return formatRemainingUsd(rem)
  return `${formatRemainingUsd(rem)} / ${formatRemainingUsd(orig)}`
}

/** % gastado: el spent_pct del sync, o derivado de remaining/original. 0..100. */
export function spentPct(spent: number | null | undefined, remaining: number | null | undefined, original: number | null | undefined): number | null {
  const clamp = (n: number) => Math.min(100, Math.max(0, n))
  if (spent != null && Number.isFinite(spent)) return clamp(spent)
  if (remaining != null && Number.isFinite(remaining) && original != null && Number.isFinite(original) && original > 0) {
    return clamp(100 * (1 - remaining / original))
  }
  return null
}
