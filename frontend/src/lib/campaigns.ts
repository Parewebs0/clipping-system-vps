import type { CampaignListItem } from '@/api/types'
import { whopSummary } from '@/lib/whop'

export function filterCampaigns(items: CampaignListItem[], status: string, q: string): CampaignListItem[] {
  const needle = q.trim().toLowerCase()
  return items.filter((c) => {
    if (status !== 'all' && c.status !== status) return false
    if (!needle) return true
    const org = whopSummary(c.source_metadata).org.toLowerCase()
    return c.name.toLowerCase().includes(needle) || org.includes(needle) || String(c.id) === needle
  })
}
