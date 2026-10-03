import { CampaignStatusBadge } from '@/components/common/StatusBadge'
import { EmptyBlock } from '@/components/common/States'
import { fmtDate } from '@/lib/format'

type Entry = { from?: string; to?: string; at?: string; by?: string; reason?: string | null }

export function StatusHistory({ sourceMetadata }: { sourceMetadata: Record<string, unknown> }) {
  const h = (Array.isArray(sourceMetadata?.status_history) ? (sourceMetadata.status_history as Entry[]) : []).slice().reverse()
  if (!h.length) return <EmptyBlock>Sin cambios manuales de estado.</EmptyBlock>
  return (
    <ol className="space-y-2">
      {h.map((e, i) => (
        <li key={i} className="flex flex-wrap items-center gap-2 rounded-md border p-2 text-sm">
          <span className="text-muted-foreground w-32 text-xs">{fmtDate(e.at)}</span>
          {e.from && <CampaignStatusBadge status={e.from} />}→{e.to && <CampaignStatusBadge status={e.to} />}
          <span className="text-muted-foreground font-mono text-xs">{e.by}</span>
          {e.reason && <span className="text-xs">«{e.reason}»</span>}
        </li>
      ))}
    </ol>
  )
}
