import { useState } from 'react'
import { Link } from 'react-router-dom'
import { PlayCircle } from 'lucide-react'
import { useCandidates } from '@/api/queries'
import type { CandidateItem } from '@/api/types'
import { ExtLink, Mono } from '@/components/common/Misc'
import { EmptyBlock, ErrorBlock, LoadingBlock } from '@/components/common/States'
import { StatusBadge, ToneBadge } from '@/components/common/StatusBadge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { fmtDate, fmtDuration } from '@/lib/format'
import { CANDIDATE_STATUS, CANDIDATE_STATUS_ORDER } from '@/lib/status'
import { fmtTimecode, previewEmbedUrl } from '@/lib/candidates'
import { ApproveCandidateButton, RejectCandidateButton } from './CandidateActions'

function Preview({ c }: { c: CandidateItem }) {
  const [show, setShow] = useState(false)
  const src = previewEmbedUrl(c.asset_source_url, c.start_time, c.end_time)
  if (!src) return null
  if (!show)
    return (
      <Button size="sm" variant="ghost" onClick={() => setShow(true)}>
        <PlayCircle className="size-4" /> Ver ventana en YouTube
      </Button>
    )
  return (
    <div className="aspect-video w-full max-w-md overflow-hidden rounded-md border">
      <iframe className="size-full" src={src} title={`preview ${c.id}`} allow="encrypted-media" allowFullScreen loading="lazy" />
    </div>
  )
}

function CandidateCard({ c, showCampaign }: { c: CandidateItem; showCampaign: boolean }) {
  const meta = CANDIDATE_STATUS[c.status]
  const excerpt = c.transcript_excerpt ?? []
  return (
    <Card className="gap-3" data-testid="candidate-card">
      <CardHeader className="gap-1">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <CardTitle className="text-base">{c.title || <span className="text-muted-foreground italic">Sin título</span>}</CardTitle>
            <div className="text-muted-foreground mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
              <span className="font-mono">
                {fmtTimecode(c.start_time)} → {fmtTimecode(c.end_time)}
              </span>
              <span>{c.duration_seconds.toFixed(1)}s</span>
              {c.score != null && <span>score {c.score.toFixed(2)}</span>}
              {c.kind && <span>{c.kind}</span>}
              {c.source && <Mono>{c.source}</Mono>}
              {showCampaign && (
                <Link className="hover:underline" to={`/campaigns/${c.campaign_id}`}>
                  {c.campaign_name ?? `#${c.campaign_id}`}
                </Link>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <ToneBadge tone={meta.tone} title={meta.help}>
              {meta.label}
            </ToneBadge>
            <RejectCandidateButton c={c} />
            <ApproveCandidateButton c={c} />
          </div>
        </div>
      </CardHeader>
      <CardContent className="grid gap-4 text-sm lg:grid-cols-2">
        <div className="space-y-3">
          {c.caption && (
            <div>
              <div className="text-muted-foreground mb-1 text-xs font-medium">Caption</div>
              <p className="whitespace-pre-line">{c.caption}</p>
            </div>
          )}
          {c.reasoning && (
            <div>
              <div className="text-muted-foreground mb-1 text-xs font-medium">Motivo del decider</div>
              <p className="text-muted-foreground">{c.reasoning}</p>
            </div>
          )}
          <div className="text-muted-foreground space-y-1 text-xs">
            <div>
              Vídeo: <ExtLink href={c.asset_source_url}>{c.asset_title || c.asset_source_url}</ExtLink>
              {c.asset_duration_seconds != null && <> · {fmtDuration(c.asset_duration_seconds)}</>}
              {c.asset_status && (
                <>
                  {' '}
                  · <StatusBadge status={c.asset_status} />
                </>
              )}
            </div>
            <div>Propuesto {fmtDate(c.created_at)}</div>
            {c.approved_at && <div>Aprobado {fmtDate(c.approved_at)}</div>}
            {c.render_job_id && (
              <div>
                Render <Mono>{c.render_job_id.slice(0, 8)}</Mono> <StatusBadge status={c.render_job_status} />
              </div>
            )}
            {c.clip_id && (
              <div>
                Clip <Mono>{c.clip_id.slice(0, 8)}</Mono>
              </div>
            )}
            {c.status === 'rejected' && (
              <div>
                Rechazado {fmtDate(c.rejected_at)}
                {c.rejected_reason && <>: {c.rejected_reason}</>}
              </div>
            )}
          </div>
          <Preview c={c} />
        </div>
        <div>
          <div className="text-muted-foreground mb-1 text-xs font-medium">Transcripción de la ventana</div>
          {excerpt.length === 0 ? (
            <p className="text-muted-foreground text-xs italic">Sin transcripción para esta ventana.</p>
          ) : (
            <ul className="bg-muted/40 space-y-1 rounded-md p-2 text-xs">
              {excerpt.map((l, i) => (
                <li key={i}>
                  <span className="text-muted-foreground font-mono">{fmtTimecode(l.start)}</span> {l.text}
                </li>
              ))}
            </ul>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

/** Candidate review (step 14). campaignId undefined = all campaigns. */
export function CandidatesPanel({ campaignId }: { campaignId?: number }) {
  const [status, setStatus] = useState<string>('pending')
  const { data, error, isLoading } = useCandidates({ campaign_id: campaignId, status: status === 'all' ? undefined : status })
  const counts = data?.counts_by_status ?? {}
  const total = Object.values(counts).reduce((a, b) => a + b, 0)
  return (
    <div>
      <Tabs value={status} onValueChange={setStatus} className="mb-4">
        <TabsList className="h-auto flex-wrap">
          {CANDIDATE_STATUS_ORDER.map((s) => (
            <TabsTrigger key={s} value={s}>
              {CANDIDATE_STATUS[s].label} ({counts[s] ?? 0})
            </TabsTrigger>
          ))}
          <TabsTrigger value="all">Todos ({total})</TabsTrigger>
        </TabsList>
      </Tabs>
      {isLoading && <LoadingBlock rows={4} />}
      {error && <ErrorBlock error={error} />}
      {data &&
        (data.items.length === 0 ? (
          <EmptyBlock>Sin candidatos con este filtro.</EmptyBlock>
        ) : (
          <div className="space-y-4">
            {data.items.map((c) => (
              <CandidateCard key={c.id} c={c} showCampaign={campaignId == null} />
            ))}
          </div>
        ))}
    </div>
  )
}
