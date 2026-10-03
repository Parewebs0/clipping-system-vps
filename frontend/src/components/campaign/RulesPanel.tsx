import { useCampaignRules } from '@/api/queries'
import { ExtLink, JsonBlock, KV } from '@/components/common/Misc'
import { ErrorBlock, LoadingBlock } from '@/components/common/States'
import { ScoreBadge } from '@/components/common/StatusBadge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

export function RulesPanel({ campaignId }: { campaignId: number }) {
  const { data, error, isLoading } = useCampaignRules(campaignId)
  if (isLoading) return <LoadingBlock />
  if (error) return <ErrorBlock error={error} />
  if (!data) return null
  const breakdown = data.priority_components as Record<string, unknown>
  const penalties = (breakdown?.penalties ?? {}) as Record<string, number>
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>Score (3c)</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <KV
            items={[
              ['Score', <ScoreBadge key="s" score={data.priority_score} eligible={breakdown?.eligible as boolean | undefined} minToRun={breakdown?.min_to_run as number | undefined} />],
              ['Base', breakdown?.base != null ? String(breakdown.base) : '—'],
              ['Penalización', breakdown?.penalty_total != null ? String(breakdown.penalty_total) : '—'],
              ['Mínimo para correr', breakdown?.min_to_run != null ? String(breakdown.min_to_run) : '—'],
              ['Preview (3a)', data.score_preview?.value != null ? String(data.score_preview.value) : '—'],
              ['CPM / 1k', data.discovered.cpm_usd_per_1k != null ? `$${data.discovered.cpm_usd_per_1k}` : '—'],
              ['Prize pool', data.discovered.prize_pool_usd != null ? `$${data.discovered.prize_pool_usd.toLocaleString('es-ES')}` : '—'],
            ]}
          />
          {Object.keys(penalties).length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(penalties).map(([k, v]) => (
                <span key={k} className="bg-muted rounded px-2 py-0.5 font-mono text-xs">
                  {k} −{v}
                </span>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Enlaces del brief ({data.asset_links_count})</CardTitle>
        </CardHeader>
        <CardContent className="space-y-1.5">
          {data.asset_links_raw.length === 0 && <div className="text-muted-foreground text-sm">Sin enlaces</div>}
          {data.asset_links_raw.map((u) => (
            <div key={u} className="text-sm">
              <ExtLink href={u}>{u}</ExtLink>
            </div>
          ))}
          {(data.brief_docs ?? []).length > 0 && <div className="text-muted-foreground pt-2 text-xs">{(data.brief_docs ?? []).length} documento(s) de brief leídos por 3a</div>}
        </CardContent>
      </Card>
      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle>Reglas extraídas (3a)</CardTitle>
        </CardHeader>
        <CardContent>
          {Object.keys(data.rules).length ? <JsonBlock value={data.rules} /> : <div className="text-muted-foreground text-sm">Sin reglas</div>}
        </CardContent>
      </Card>
      {data.card_text && (
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Texto del brief</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm whitespace-pre-wrap">{data.card_text}</p>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
