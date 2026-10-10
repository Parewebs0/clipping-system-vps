import { Link, useParams } from 'react-router-dom'
import { useShadowRun } from '@/api/queries'
import type { ShadowAsset, ShadowGraphSide, ShadowSide } from '@/api/types'
import { PageHeader } from '@/components/common/PageHeader'
import { StatCard } from '@/components/common/StatCard'
import { EmptyBlock, ErrorBlock, LoadingBlock } from '@/components/common/States'
import { ToneBadge } from '@/components/common/StatusBadge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { fmtDate, fmtPct, fmtUsd } from '@/lib/format'

function win(w?: number[][] | null) {
  if (!w?.length) return '—'
  return w.map((p) => `${p[0]}–${p[1]}s`).join(' · ')
}

function ClipCol({ title, side, extra }: { title: string; side: ShadowSide; extra?: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="mb-2 text-sm font-semibold">{title}</div>
      <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1.5 text-sm">
        <dt className="text-muted-foreground">Clips</dt>
        <dd className="tabular-nums">{side.n_clips}</dd>
        <dt className="text-muted-foreground">Ventanas</dt>
        <dd className="font-mono text-xs">{win(side.windows)}</dd>
        <dt className="text-muted-foreground">Títulos</dt>
        <dd>{side.titles?.length ? side.titles.join(' · ') : '—'}</dd>
        <dt className="text-muted-foreground">Captions</dt>
        <dd className="text-muted-foreground">{side.captions?.length ? side.captions.join(' · ') : '—'}</dd>
        <dt className="text-muted-foreground">Coste</dt>
        <dd className="tabular-nums">{fmtUsd(side.cost_usd)}</dd>
        <dt className="text-muted-foreground">Latencia</dt>
        <dd className="tabular-nums">{side.latency_ms} ms</dd>
      </dl>
      {extra}
    </div>
  )
}

function GraphExtras({ g }: { g: ShadowGraphSide }) {
  return (
    <div className="mt-3 space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {(g.graph_path ?? []).map((n, i) => (
          <span key={`${n}-${i}`} className="bg-muted rounded px-1.5 py-0.5 font-mono text-[11px]">
            {n}
          </span>
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        <ToneBadge tone={g.asked_more_context ? 'amber' : 'zinc'}>
          {g.asked_more_context ? 'pidió más contexto' : 'sin más contexto'}
        </ToneBadge>
        <ToneBadge tone={g.would_request_approval ? 'rose' : 'zinc'}>
          {g.would_request_approval ? 'habría pedido aprobación' : 'sin aprobación'}
        </ToneBadge>
      </div>
      {g.decision_reason && <p className="text-muted-foreground text-xs">{g.decision_reason}</p>}
      {g.approval_reason && <p className="text-xs text-rose-800">{g.approval_reason}</p>}
    </div>
  )
}

function AssetCard({ a }: { a: ShadowAsset }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          {a.label || a.asset_id.slice(0, 8)}
          <ToneBadge tone={a.diff.iou >= 0.8 ? 'emerald' : a.diff.iou >= 0.4 ? 'amber' : 'rose'}>
            IoU {a.diff.iou.toFixed(2)}
          </ToneBadge>
        </CardTitle>
        <CardDescription className="font-mono text-xs">
          {a.asset_id}
          {a.campaign_id != null ? ` · campaña ${a.campaign_id}` : ''}
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-6 md:grid-cols-2">
        <ClipCol title="Decider actual" side={a.current} />
        <ClipCol title="LangGraph" side={a.graph} extra={<GraphExtras g={a.graph} />} />
      </CardContent>
    </Card>
  )
}

export function ShadowRunDetailPage() {
  const { id = '' } = useParams()
  const { data, error, isLoading } = useShadowRun(id)
  return (
    <>
      <PageHeader
        eyebrow="Sombra"
        title={data?.id ?? id}
        description={
          <span>
            <Link to="/shadow" className="hover:underline">
              Todas las ejecuciones
            </Link>
            {data?.generated_at ? ` · ${fmtDate(data.generated_at)}` : ''}
          </span>
        }
      />
      {isLoading && <LoadingBlock rows={6} />}
      {error && <ErrorBlock error={error} />}
      {data && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <StatCard label="Assets" value={data.n_assets} />
            <StatCard label="Coincidencia" value={fmtPct(data.match_pct)} hint={`IoU medio ${data.mean_iou.toFixed(2)}`} />
            <StatCard label="Coste total" value={fmtUsd(data.cost_usd_total)} />
            <StatCard label="Latencia total" value={`${data.latency_ms_total} ms`} />
          </div>
          {(data.assets ?? []).length === 0 ? (
            <EmptyBlock>El JSONL no tiene filas.</EmptyBlock>
          ) : (
            (data.assets ?? []).map((a) => <AssetCard key={a.asset_id} a={a} />)
          )}
        </div>
      )}
    </>
  )
}
