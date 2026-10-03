import { useState } from 'react'
import { useJobs } from '@/api/queries'
import type { JobRecentItem } from '@/api/types'
import { JsonBlock, KV, Mono } from '@/components/common/Misc'
import { PageHeader } from '@/components/common/PageHeader'
import { EmptyBlock, ErrorBlock, LoadingBlock } from '@/components/common/States'
import { StatusBadge } from '@/components/common/StatusBadge'
import { Card, CardContent } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { fmtDate, fmtDuration } from '@/lib/format'
import { JOB_STATUS } from '@/lib/status'

const JOB_TYPES = ['download', 'transcribe', 'render', 'qa', 'publish']
const ALL = '__all'

export function JobsPage() {
  const [jobType, setJobType] = useState(ALL)
  const [status, setStatus] = useState(ALL)
  const [open, setOpen] = useState<JobRecentItem | null>(null)
  const { data, error, isLoading } = useJobs({ job_type: jobType === ALL ? undefined : jobType, status: status === ALL ? undefined : status })

  return (
    <>
      <PageHeader
        eyebrow="Worker"
        title="Jobs"
        description="Últimos 200 jobs por fecha de actualización · auto-refresh 10 s"
        actions={
          <>
            <Select value={jobType} onValueChange={setJobType}>
              <SelectTrigger className="w-40" aria-label="Tipo de job">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>Todos los tipos</SelectItem>
                {JOB_TYPES.map((t) => (
                  <SelectItem key={t} value={t}>
                    {t}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger className="w-40" aria-label="Estado">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>Todos los estados</SelectItem>
                {Object.keys(JOB_STATUS).map((s) => (
                  <SelectItem key={s} value={s}>
                    {s}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </>
        }
      />
      {isLoading && <LoadingBlock rows={8} />}
      {error && <ErrorBlock error={error} />}
      {data &&
        (data.items.length === 0 ? (
          <EmptyBlock>Sin jobs con estos filtros.</EmptyBlock>
        ) : (
          <Card className="py-0">
            <CardContent className="px-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="pl-4">Job</TableHead>
                    <TableHead>Tipo</TableHead>
                    <TableHead>Estado</TableHead>
                    <TableHead>Campaña</TableHead>
                    <TableHead className="text-right">Intentos</TableHead>
                    <TableHead className="text-right">Duración</TableHead>
                    <TableHead>Error</TableHead>
                    <TableHead className="pr-4">Actualizado</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.items.map((j) => (
                    <TableRow key={j.id} className="cursor-pointer" onClick={() => setOpen(j)}>
                      <TableCell className="pl-4">
                        <Mono>{j.id.slice(0, 8)}</Mono>
                      </TableCell>
                      <TableCell className="font-mono text-xs">{j.job_type}</TableCell>
                      <TableCell>
                        <StatusBadge status={j.status} />
                      </TableCell>
                      <TableCell className="max-w-48 truncate text-xs">{j.campaign_name ?? '—'}</TableCell>
                      <TableCell className="text-right text-xs tabular-nums">
                        {j.attempts}/{j.max_attempts}
                      </TableCell>
                      <TableCell className="text-right text-xs tabular-nums">{fmtDuration(j.elapsed_seconds)}</TableCell>
                      <TableCell className="max-w-64 truncate text-xs text-rose-700" title={j.error_message ?? undefined}>
                        {j.error_message ?? ''}
                      </TableCell>
                      <TableCell className="pr-4 text-xs whitespace-nowrap">{fmtDate(j.updated_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        ))}
      <Dialog open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
          {open && (
            <>
              <DialogHeader>
                <DialogTitle className="flex items-center gap-2">
                  {open.job_type} <StatusBadge status={open.status} />
                </DialogTitle>
                <DialogDescription className="font-mono text-xs">{open.id}</DialogDescription>
              </DialogHeader>
              <KV
                items={[
                  ['Campaña', open.campaign_name ?? '—'],
                  ['Worker', open.worker_id ?? '—'],
                  ['Prioridad', String(open.priority)],
                  ['Creado', fmtDate(open.created_at)],
                  ['Iniciado', fmtDate(open.started_at)],
                  ['Completado', fmtDate(open.completed_at)],
                  ['Error', open.error_message ?? '—'],
                ]}
              />
              <div className="grid gap-3 md:grid-cols-2">
                <div>
                  <div className="mb-1 text-xs font-medium">payload</div>
                  <JsonBlock value={open.payload} />
                </div>
                <div>
                  <div className="mb-1 text-xs font-medium">result</div>
                  <JsonBlock value={open.result} />
                </div>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}
