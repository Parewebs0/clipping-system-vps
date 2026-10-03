// Helpers over the Whop discovery payload stored in campaigns.source_metadata
// (top level and/or source_metadata.discovered). Shapes are JSONB, so every
// accessor is defensive.
type Dict = Record<string, unknown>

const asDict = (v: unknown): Dict => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Dict) : {})
const asList = (v: unknown): unknown[] => (Array.isArray(v) ? v : [])

export interface WhopSummary {
  org: string
  orgVerified?: boolean
  platforms: string[]
  bestCpm: number | null
  prizePool: number | null
  refs: { url: string; label?: string }[]
  whopStatus?: string
  detailUrl?: string
}

export function whopSummary(sourceMetadata: unknown): WhopSummary {
  const sm = asDict(sourceMetadata)
  const d = asDict(sm.discovered)
  const pick = (k: string) => (sm[k] !== undefined && sm[k] !== null && sm[k] !== '' ? sm[k] : d[k])
  const payouts = asList(pick('payouts')).map(asDict)
  let bestCpm: number | null = null
  for (const p of payouts) {
    const c = typeof p.rate_cents === 'number' ? p.rate_cents / 100 : null
    if (c != null && (bestCpm == null || c < bestCpm)) bestCpm = c
  }
  if (bestCpm == null) {
    const c = Number(pick('cpm_usd_per_1k'))
    bestCpm = Number.isFinite(c) && c > 0 ? c : null
  }
  const prize = Number(pick('prize_pool_usd'))
  const refs = asList(pick('reference_materials'))
    .map((r) => (typeof r === 'string' ? { url: r } : { url: String(asDict(r).url ?? ''), label: (asDict(r).label ?? asDict(r).title) as string | undefined }))
    .filter((r) => r.url)
  return {
    org: String(pick('organization_name') ?? ''),
    orgVerified: typeof pick('organization_verified') === 'boolean' ? (pick('organization_verified') as boolean) : undefined,
    platforms: asList(pick('platforms')).map(String),
    bestCpm,
    prizePool: Number.isFinite(prize) && prize > 0 ? prize : null,
    refs,
    whopStatus: pick('status') ? String(pick('status')) : undefined,
    detailUrl: d.detail_url ? String(d.detail_url) : undefined,
  }
}
