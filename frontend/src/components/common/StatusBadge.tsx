import { Badge } from '@/components/ui/badge'
import { anyStatusMeta, campaignStatusMeta, TONE_CLASS, type Tone } from '@/lib/status'
import { cn } from '@/lib/utils'

export function ToneBadge({ tone, children, className, title }: { tone: Tone; children: React.ReactNode; className?: string; title?: string }) {
  return (
    <Badge variant="outline" title={title} className={cn('font-medium', TONE_CLASS[tone], className)}>
      {children}
    </Badge>
  )
}

export function CampaignStatusBadge({ status, className }: { status: string; className?: string }) {
  const m = campaignStatusMeta(status)
  return (
    <ToneBadge tone={m.tone} className={className} title={m.help ? `${status}: ${m.help}` : status}>
      {m.label}
    </ToneBadge>
  )
}

export function StatusBadge({ status, className }: { status?: string | null; className?: string }) {
  const m = anyStatusMeta(status)
  return (
    <ToneBadge tone={m.tone} className={className}>
      {m.label}
    </ToneBadge>
  )
}

export function ScoreBadge({ score, eligible, minToRun }: { score?: number | null; eligible?: boolean | null; minToRun?: number | null }) {
  if (score == null) return null
  const tone: Tone = score >= 70 ? 'emerald' : score >= (minToRun ?? 50) ? 'amber' : 'rose'
  return (
    <ToneBadge tone={tone} title={`score ${score}${minToRun != null ? ` (mínimo ${minToRun})` : ''}${eligible === false ? ' · no elegible' : ''}`}>
      <span className="font-semibold tabular-nums">{score.toFixed(0)}</span>
      {eligible === false && <span className="opacity-70">· no elegible</span>}
    </ToneBadge>
  )
}
