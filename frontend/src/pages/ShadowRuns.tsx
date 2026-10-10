import { Link, useNavigate } from 'react-router-dom'
import { GitCompare } from 'lucide-react'
import { useShadowRuns } from '@/api/queries'
import { PageHeader } from '@/components/common/PageHeader'
import { EmptyBlock, ErrorBlock, LoadingBlock } from '@/components/common/States'
import { ToneBadge } from '@/components/common/StatusBadge'
import { Card, CardContent } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { fmtDate, fmtPct, fmtUsd } from '@/lib/format'

export function ShadowRunsPage() {
  const { data, error, isLoading } = useShadowRuns()
  const navigate = useNavigate()
  return (
    <>
      <PageHeader
        eyebrow="Sombra"
        title="Clip-decider LangGraph"
        description="Solo lectura. Informes JSONL comparados con el decider actual, sin escribir en la BD."
      />
      {isLoading && <LoadingBlock rows={5} />}
      {error && <ErrorBlock error={error} />}
      {data && data.items.length === 0 && (
        <EmptyBlock>No hay informes en reports/shadow. Corre python scripts/shadow_decider.py --limit 5</EmptyBlock>
      )}
      {data && data.items.length > 0 && (
        <Card className="py-0">
          <CardContent className="px-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-4">Ejecución</TableHead>
                  <TableHead>Fecha</TableHead>
                  <TableHead className="text-right">Assets</TableHead>
                  <TableHead className="text-right">Coincidencia</TableHead>
                  <TableHead className="text-right">Coste</TableHead>
                  <TableHead className="pr-4 text-right">Latencia</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.items.map((run) => (
                  <TableRow key={run.id} className="cursor-pointer" onClick={() => navigate(`/shadow/${run.id}`)}>
                    <TableCell className="pl-4">
                      <Link to={`/shadow/${run.id}`} className="font-medium hover:underline" onClick={(e) => e.stopPropagation()}>
                        {run.id}
                      </Link>
                      <div className="text-muted-foreground font-mono text-xs">{run.filename}</div>
                    </TableCell>
                    <TableCell>{fmtDate(run.generated_at)}</TableCell>
                    <TableCell className="text-right tabular-nums">{run.n_assets}</TableCell>
                    <TableCell className="text-right">
                      <ToneBadge tone={run.match_pct >= 80 ? 'emerald' : run.match_pct >= 50 ? 'amber' : 'rose'}>
                        {fmtPct(run.match_pct)}
                      </ToneBadge>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{fmtUsd(run.cost_usd_total)}</TableCell>
                    <TableCell className="pr-4 text-right tabular-nums">{run.latency_ms_total} ms</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
      <p className="text-muted-foreground mt-4 flex items-center gap-2 text-xs">
        <GitCompare className="size-3.5" />
        Coincidencia = IoU medio de ventanas frente al decider actual.
      </p>
    </>
  )
}
