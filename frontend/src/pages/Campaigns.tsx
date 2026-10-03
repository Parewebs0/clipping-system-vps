import { useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Search } from 'lucide-react'
import { useCampaigns } from '@/api/queries'
import { CreateCampaignDialog } from '@/components/campaign/CreateCampaignDialog'
import { PageHeader } from '@/components/common/PageHeader'
import { EmptyBlock, ErrorBlock, LoadingBlock } from '@/components/common/States'
import { CampaignStatusBadge, ScoreBadge, ToneBadge } from '@/components/common/StatusBadge'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { fmtAgo } from '@/lib/format'
import { CAMPAIGN_STATUS_ORDER, campaignStatusMeta } from '@/lib/status'
import { filterCampaigns } from '@/lib/campaigns'
import { whopSummary } from '@/lib/whop'

export function CampaignsPage() {
  const { data, error, isLoading } = useCampaigns()
  const [params, setParams] = useSearchParams()
  const status = params.get('status') ?? 'all'
  const [q, setQ] = useState('')
  const navigate = useNavigate()

  const items = useMemo(() => data?.items ?? [], [data])
  const counts = useMemo(() => {
    const m: Record<string, number> = {}
    for (const c of items) m[c.status] = (m[c.status] ?? 0) + 1
    return m
  }, [items])
  const rows = useMemo(() => filterCampaigns(items, status, q), [items, status, q])
  const statuses = [...CAMPAIGN_STATUS_ORDER, ...Object.keys(counts).filter((s) => !(CAMPAIGN_STATUS_ORDER as string[]).includes(s))]

  return (
    <>
      <PageHeader eyebrow="Plataforma" title="Campañas" description={data ? `${data.count} campañas` : undefined} actions={<CreateCampaignDialog />} />
      {isLoading && <LoadingBlock rows={8} />}
      {error && <ErrorBlock error={error} />}
      {data && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Tabs value={status} onValueChange={(v) => setParams(v === 'all' ? {} : { status: v })}>
              <TabsList className="h-auto flex-wrap">
                <TabsTrigger value="all">
                  Todas <span className="text-muted-foreground ml-1 tabular-nums">{items.length}</span>
                </TabsTrigger>
                {statuses.map((s) => (
                  <TabsTrigger key={s} value={s}>
                    {campaignStatusMeta(s).label} <span className="text-muted-foreground ml-1 tabular-nums">{counts[s] ?? 0}</span>
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
            <div className="relative">
              <Search className="text-muted-foreground absolute top-2.5 left-2.5 size-4" />
              <Input placeholder="Buscar nombre, organización o id" value={q} onChange={(e) => setQ(e.target.value)} className="w-72 pl-8" />
            </div>
          </div>
          {rows.length === 0 ? (
            <EmptyBlock>No hay campañas con este filtro.</EmptyBlock>
          ) : (
            <Card className="py-0">
              <CardContent className="px-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-14 pl-4">ID</TableHead>
                      <TableHead>Campaña</TableHead>
                      <TableHead>Estado</TableHead>
                      <TableHead>Score</TableHead>
                      <TableHead>Origen</TableHead>
                      <TableHead className="text-right">Assets</TableHead>
                      <TableHead className="text-right">Clips QA</TableHead>
                      <TableHead className="pr-4 text-right">Actualizada</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {rows.map((c) => {
                      const w = whopSummary(c.source_metadata)
                      const err = c.resolve_error ?? c.briefing_error
                      return (
                        <TableRow key={c.id} className="cursor-pointer" onClick={() => navigate(`/campaigns/${c.id}`)}>
                          <TableCell className="text-muted-foreground pl-4 tabular-nums">{c.id}</TableCell>
                          <TableCell className="max-w-sm">
                            <Link to={`/campaigns/${c.id}`} className="block truncate font-medium hover:underline" onClick={(e) => e.stopPropagation()}>
                              {c.name}
                            </Link>
                            <div className="text-muted-foreground flex items-center gap-2 truncate text-xs">
                              {w.org || '—'}
                              {w.bestCpm != null && <span>· ${w.bestCpm.toFixed(2)} CPM</span>}
                              {w.platforms.length > 0 && <span>· {w.platforms.slice(0, 3).join(', ')}</span>}
                            </div>
                          </TableCell>
                          <TableCell>
                            <div className="flex flex-col items-start gap-1">
                              <CampaignStatusBadge status={c.status} />
                              {err?.kind && (
                                <ToneBadge tone="zinc" className="font-mono text-[10px]" title={err.message ?? undefined}>
                                  {err.kind}
                                </ToneBadge>
                              )}
                            </div>
                          </TableCell>
                          <TableCell>
                            <ScoreBadge score={c.priority_score} eligible={c.score_eligible} minToRun={c.score_min_to_run} />
                          </TableCell>
                          <TableCell className="text-xs">{c.source_provider}</TableCell>
                          <TableCell className="text-right text-xs tabular-nums">
                            {c.assets_transcribed}/{c.assets_total}
                          </TableCell>
                          <TableCell className="text-right text-xs tabular-nums">
                            {c.clips_approved_qa}/{c.clips_total}
                          </TableCell>
                          <TableCell className="text-muted-foreground pr-4 text-right text-xs whitespace-nowrap">{fmtAgo(c.updated_at)}</TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </>
  )
}
