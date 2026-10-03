import { Link, useParams } from 'react-router-dom'
import { AlertTriangle, ArrowLeft, Coins } from 'lucide-react'
import { useCampaign, usePipeline } from '@/api/queries'
import type { CampaignDetail, PipelineError } from '@/api/types'
import { ActiveJobsTable, AssetsTable, ClipsTable, PipelineTable } from '@/components/campaign/Tables'
import { RulesPanel } from '@/components/campaign/RulesPanel'
import { ExtLink, JsonBlock, KV } from '@/components/common/Misc'
import { PageHeader } from '@/components/common/PageHeader'
import { ErrorBlock, LoadingBlock } from '@/components/common/States'
import { CampaignStatusBadge, ToneBadge } from '@/components/common/StatusBadge'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { fmtDate, fmtUsd } from '@/lib/format'
import { campaignStatusMeta, errorHint } from '@/lib/status'
import { whopSummary } from '@/lib/whop'

function ErrorPanel({ title, err }: { title: string; err?: PipelineError | null }) {
  if (!err) return null
  return (
    <Alert variant="destructive">
      <AlertTriangle className="size-4" />
      <AlertTitle className="flex items-center gap-2">
        {title} <span className="bg-destructive/10 rounded px-1.5 font-mono text-xs">{err.kind ?? 'unknown'}</span>
      </AlertTitle>
      <AlertDescription>
        <p className="font-medium">{err.message ?? '(sin mensaje)'}</p>
        <p>
          <span className="opacity-70">Acción sugerida:</span> {errorHint(err.kind)}
        </p>
        {err.at && <p className="text-xs opacity-70">Detectado {fmtDate(err.at)}</p>}
      </AlertDescription>
    </Alert>
  )
}

function Summary({ d }: { d: CampaignDetail }) {
  const c = d.campaign
  const w = whopSummary(c.source_metadata)
  const spec = c.spec as Record<string, unknown>
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card>
        <CardHeader>
          <CardTitle>Origen</CardTitle>
        </CardHeader>
        <CardContent>
          <KV
            items={[
              ['Proveedor', c.source_provider],
              ['Source ID', c.source_id ?? '—'],
              ['URL', <ExtLink key="u" href={c.source_url} />],
              ['Organización', w.org ? `${w.org}${w.orgVerified ? ' ✓' : ''}` : '—'],
              ['Estado Whop', w.whopStatus ?? '—'],
              ['Plataformas', w.platforms.join(', ') || '—'],
              ['Mejor CPM', w.bestCpm != null ? `$${w.bestCpm.toFixed(2)}` : '—'],
              ['Prize pool', w.prizePool != null ? `$${w.prizePool.toLocaleString('es-ES')}` : '—'],
            ]}
          />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Spec</CardTitle>
        </CardHeader>
        <CardContent>
          <KV
            items={[
              ['Duración', spec.duration_min != null || spec.duration_max != null ? `${spec.duration_min ?? '?'}–${spec.duration_max ?? '?'} s` : '—'],
              ['Formato', (spec.format as string) ?? '—'],
              ['Idioma', (spec.language as string) ?? '—'],
              ['Subtítulos', spec.captions_required ? 'obligatorios' : 'no'],
              ['Keywords', ((spec.keywords as string[]) ?? []).join(', ') || '—'],
              ['Excluir', ((spec.exclude_keywords as string[]) ?? []).join(', ') || '—'],
            ]}
          />
          {c.source_instructions && <p className="text-muted-foreground mt-3 text-xs whitespace-pre-wrap">{c.source_instructions}</p>}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Coins className="size-4" /> Coste LLM
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-semibold tabular-nums">{fmtUsd(d.llm_usage.cost_usd)}</div>
          <div className="text-muted-foreground mb-3 text-xs">
            {d.llm_usage.calls} llamadas · {d.llm_usage.errors} errores · {d.llm_usage.total_tokens.toLocaleString('es-ES')} tokens
          </div>
          <div className="space-y-1">
            {(d.llm_usage.by_stage ?? []).map((s) => (
              <div key={s.stage} className="flex justify-between text-xs">
                <span className="font-mono">{s.stage}</span>
                <span className="tabular-nums">
                  {s.calls} · {fmtUsd(s.cost_usd)}
                </span>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
      {w.refs.length > 0 && (
        <Card className="lg:col-span-3">
          <CardHeader>
            <CardTitle>Reference materials ({w.refs.length})</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-1.5 sm:grid-cols-2">
            {w.refs.map((r) => (
              <ExtLink key={r.url} href={r.url} className="text-sm">
                {r.label || r.url}
              </ExtLink>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  )
}

export function CampaignDetailPage({ renderActions }: { renderActions?: (d: CampaignDetail) => React.ReactNode }) {
  const id = Number(useParams().id)
  const { data, error, isLoading } = useCampaign(id)
  const pipeline = usePipeline(id, !!data)
  const c = data?.campaign

  return (
    <>
      <Button asChild variant="ghost" size="sm" className="mb-2 -ml-2">
        <Link to="/campaigns">
          <ArrowLeft className="size-4" /> Campañas
        </Link>
      </Button>
      {isLoading && <LoadingBlock rows={6} />}
      {error && <ErrorBlock error={error} />}
      {data && c && (
        <>
          <PageHeader
            eyebrow={`Campaña #${c.id}`}
            title={c.name}
            description={
              <span className="flex flex-wrap items-center gap-2">
                <CampaignStatusBadge status={c.status} />
                <span>{campaignStatusMeta(c.status).help}</span>
                <ToneBadge tone="slate">{c.source_provider}</ToneBadge>
                <span className="text-xs">actualizada {fmtDate(c.updated_at)}</span>
              </span>
            }
            actions={renderActions?.(data)}
          />
          <div className="mb-6 space-y-3">
            <ErrorPanel title="Brief-reader (3a)" err={c.briefing_error} />
            <ErrorPanel title="Resolver (3b)" err={c.resolve_error} />
          </div>
          <Tabs defaultValue="summary">
            <TabsList className="h-auto flex-wrap">
              <TabsTrigger value="summary">Resumen</TabsTrigger>
              <TabsTrigger value="assets">Assets ({data.assets.length})</TabsTrigger>
              <TabsTrigger value="pipeline">Pipeline</TabsTrigger>
              <TabsTrigger value="clips">Clips ({data.clips.length})</TabsTrigger>
              <TabsTrigger value="jobs">Jobs activos ({data.active_jobs.length})</TabsTrigger>
              <TabsTrigger value="rules">Reglas y score</TabsTrigger>
              <TabsTrigger value="raw">Metadata</TabsTrigger>
            </TabsList>
            <TabsContent value="summary" className="mt-4">
              <Summary d={data} />
            </TabsContent>
            <TabsContent value="assets" className="mt-4">
              <Card className="py-0">
                <CardContent className="px-0">
                  <AssetsTable rows={data.assets} baseUrl={data.worker_file_base_url} />
                </CardContent>
              </Card>
            </TabsContent>
            <TabsContent value="pipeline" className="mt-4">
              <Card className="py-0">
                <CardContent className="px-0">{pipeline.isLoading ? <LoadingBlock /> : <PipelineTable data={pipeline.data} />}</CardContent>
              </Card>
            </TabsContent>
            <TabsContent value="clips" className="mt-4">
              <Card className="py-0">
                <CardContent className="px-0">
                  <ClipsTable rows={data.clips} baseUrl={data.worker_file_base_url} />
                </CardContent>
              </Card>
            </TabsContent>
            <TabsContent value="jobs" className="mt-4">
              <Card className="py-0">
                <CardContent className="px-0">
                  <ActiveJobsTable rows={data.active_jobs} />
                </CardContent>
              </Card>
            </TabsContent>
            <TabsContent value="rules" className="mt-4">
              <RulesPanel campaignId={id} />
            </TabsContent>
            <TabsContent value="raw" className="mt-4 grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle>source_metadata</CardTitle>
                </CardHeader>
                <CardContent>
                  <JsonBlock value={c.source_metadata} />
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>spec</CardTitle>
                </CardHeader>
                <CardContent>
                  <JsonBlock value={c.spec} />
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
        </>
      )}
    </>
  )
}
