import { Link } from 'react-router-dom'
import { AlertTriangle, Briefcase, Clapperboard, Coins, HardDrive, ListChecks } from 'lucide-react'
import { useOverview } from '@/api/queries'
import type { LlmUsageSummary } from '@/api/types'
import { PageHeader } from '@/components/common/PageHeader'
import { StatCard } from '@/components/common/StatCard'
import { EmptyBlock, ErrorBlock, LoadingBlock } from '@/components/common/States'
import { CampaignStatusBadge, StatusBadge } from '@/components/common/StatusBadge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { fmtAgo, fmtDate, fmtUsd } from '@/lib/format'
import { CAMPAIGN_STATUS_ORDER } from '@/lib/status'

function CountList({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data)
  if (!entries.length) return <div className="text-muted-foreground text-sm">Sin datos</div>
  return (
    <div className="flex flex-wrap gap-2">
      {entries.map(([k, v]) => (
        <div key={k} className="flex items-center gap-1.5">
          <StatusBadge status={k} />
          <span className="text-sm font-semibold tabular-nums">{v}</span>
        </div>
      ))}
    </div>
  )
}

function LlmTable({ u }: { u: LlmUsageSummary }) {
  if (!u.by_stage?.length) return <div className="text-muted-foreground text-sm">Sin llamadas registradas</div>
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Stage</TableHead>
          <TableHead className="text-right">Llamadas</TableHead>
          <TableHead className="text-right">Errores</TableHead>
          <TableHead className="text-right">Tokens</TableHead>
          <TableHead className="text-right">Coste</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {(u.by_stage ?? []).map((s) => (
          <TableRow key={s.stage}>
            <TableCell className="font-mono text-xs">{s.stage}</TableCell>
            <TableCell className="text-right tabular-nums">{s.calls}</TableCell>
            <TableCell className="text-right tabular-nums">{s.errors}</TableCell>
            <TableCell className="text-right tabular-nums">{s.total_tokens.toLocaleString('es-ES')}</TableCell>
            <TableCell className="text-right tabular-nums">{fmtUsd(s.cost_usd)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

export function OverviewPage() {
  const { data, error, isLoading, dataUpdatedAt } = useOverview()
  return (
    <>
      <PageHeader
        eyebrow="Plataforma"
        title="Overview"
        description={dataUpdatedAt ? `Auto-refresh cada 10 s · actualizado ${fmtDate(new Date(dataUpdatedAt).toISOString())}` : undefined}
      />
      {isLoading && <LoadingBlock rows={6} />}
      {error && <ErrorBlock error={error} />}
      {data && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
            <StatCard label="Campañas" value={data.total_campaigns} icon={<Briefcase className="size-4" />} />
            <StatCard label="Jobs (24 h)" value={data.total_jobs_last_24h} icon={<ListChecks className="size-4" />} />
            <StatCard label="Clips (24 h)" value={data.total_clips_last_24h} icon={<Clapperboard className="size-4" />} />
            <StatCard label="Vídeos sin disco" value={data.disk_unavailable_videos} icon={<HardDrive className="size-4" />} hint="assets sin local_path y no pending" />
            <StatCard
              label="Coste LLM (24 h)"
              value={fmtUsd(data.llm_usage_24h.cost_usd)}
              icon={<Coins className="size-4" />}
              hint={`${data.llm_usage_24h.calls} llamadas · total ${fmtUsd(data.llm_usage_total.cost_usd)}`}
            />
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Pipeline de campañas</CardTitle>
              <CardDescription>Estados pipeline v2 (discovered → briefed → assets_resolved → scored) y terminales.</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
                {CAMPAIGN_STATUS_ORDER.map((s) => (
                  <Link key={s} to={`/campaigns?status=${s}`} className="hover:bg-muted rounded-lg border p-3 transition-colors">
                    <CampaignStatusBadge status={s} />
                    <div className="mt-2 text-2xl font-semibold tabular-nums">{data.campaigns_by_status[s] ?? 0}</div>
                  </Link>
                ))}
              </div>
            </CardContent>
          </Card>

          <div className="grid gap-6 lg:grid-cols-3">
            <Card>
              <CardHeader>
                <CardTitle>Assets</CardTitle>
              </CardHeader>
              <CardContent>
                <CountList data={data.assets_by_status} />
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Clips por QA</CardTitle>
              </CardHeader>
              <CardContent>
                <CountList data={data.clips_by_qa_status} />
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Clips por estado</CardTitle>
              </CardHeader>
              <CardContent>
                <CountList data={data.clips_by_status} />
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Jobs últimas 24 h</CardTitle>
              </CardHeader>
              <CardContent>
                {Object.keys(data.jobs_by_type_status).length === 0 ? (
                  <EmptyBlock>Sin jobs en las últimas 24 h</EmptyBlock>
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Tipo</TableHead>
                        <TableHead>Estados</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {Object.entries(data.jobs_by_type_status).map(([t, st]) => (
                        <TableRow key={t}>
                          <TableCell className="font-mono text-xs">{t}</TableCell>
                          <TableCell>
                            <CountList data={st} />
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Uso de LLM (total)</CardTitle>
                <CardDescription>
                  {data.llm_usage_total.calls} llamadas · {data.llm_usage_total.total_tokens.toLocaleString('es-ES')} tokens · última {fmtAgo(data.llm_usage_total.last_call_at)}
                </CardDescription>
              </CardHeader>
              <CardContent>
                <LlmTable u={data.llm_usage_total} />
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <AlertTriangle className="size-4 text-rose-600" /> Errores recientes de jobs
              </CardTitle>
            </CardHeader>
            <CardContent>
              {data.recent_errors.length === 0 ? (
                <EmptyBlock>Sin errores</EmptyBlock>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Cuándo</TableHead>
                      <TableHead>Tipo</TableHead>
                      <TableHead>Mensaje</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.recent_errors.map((e) => (
                      <TableRow key={e.id}>
                        <TableCell className="whitespace-nowrap">{fmtDate(e.created_at)}</TableCell>
                        <TableCell className="font-mono text-xs">{e.job_type}</TableCell>
                        <TableCell className="max-w-xl text-xs break-words whitespace-normal">{e.error_message}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </>
  )
}
