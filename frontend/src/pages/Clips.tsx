import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useClips } from '@/api/queries'
import type { ClipInventoryItem } from '@/api/types'
import { ExtLink, JsonBlock, KV, Mono, PathCell } from '@/components/common/Misc'
import { PageHeader } from '@/components/common/PageHeader'
import { EmptyBlock, ErrorBlock, LoadingBlock } from '@/components/common/States'
import { StatusBadge, ToneBadge } from '@/components/common/StatusBadge'
import { Card, CardContent } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { fmtBytes, fmtDate, fmtDuration } from '@/lib/format'
import { CLIP_QA_STATUS } from '@/lib/status'

export function ClipsPage() {
  const [qa, setQa] = useState('all')
  const [open, setOpen] = useState<ClipInventoryItem | null>(null)
  const { data, error, isLoading } = useClips({ qa_status: qa === 'all' ? undefined : qa })
  return (
    <>
      <PageHeader eyebrow="Worker" title="Clips" description="Clips renderizados, resultado de QA y ubicación (pending_upload / uploaded / archived)" />
      <Tabs value={qa} onValueChange={setQa} className="mb-4">
        <TabsList>
          <TabsTrigger value="all">Todos</TabsTrigger>
          {Object.entries(CLIP_QA_STATUS).map(([k, m]) => (
            <TabsTrigger key={k} value={k}>
              {m.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      {isLoading && <LoadingBlock rows={8} />}
      {error && <ErrorBlock error={error} />}
      {data &&
        (data.items.length === 0 ? (
          <EmptyBlock>Sin clips con este filtro.</EmptyBlock>
        ) : (
          <Card className="py-0">
            <CardContent className="px-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="pl-4">Clip</TableHead>
                    <TableHead>Campaña</TableHead>
                    <TableHead>QA</TableHead>
                    <TableHead>Estado</TableHead>
                    <TableHead>Ubicación</TableHead>
                    <TableHead>Fichero</TableHead>
                    <TableHead className="text-right">Duración</TableHead>
                    <TableHead className="pr-4">Creado</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.items.map((c) => (
                    <TableRow key={c.id} className="cursor-pointer" onClick={() => setOpen(c)}>
                      <TableCell className="pl-4">
                        <Mono>{c.id.slice(0, 8)}</Mono>
                      </TableCell>
                      <TableCell className="max-w-48 truncate">
                        <Link className="hover:underline" to={`/campaigns/${c.campaign_id}`} onClick={(e) => e.stopPropagation()}>
                          {c.campaign_name ?? c.campaign_id}
                        </Link>
                      </TableCell>
                      <TableCell>
                        <ToneBadge tone={CLIP_QA_STATUS[c.qa_status].tone}>{CLIP_QA_STATUS[c.qa_status].label}</ToneBadge>
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={c.status} />
                      </TableCell>
                      <TableCell>{c.location ? <StatusBadge status={c.location} /> : <span className="text-muted-foreground text-xs">—</span>}</TableCell>
                      <TableCell className="max-w-xs">
                        <PathCell path={c.final_path_worker ?? c.file_path} baseUrl={data.worker_file_base_url} />
                      </TableCell>
                      <TableCell className="text-right text-xs tabular-nums">{fmtDuration(c.duration_seconds)}</TableCell>
                      <TableCell className="pr-4 text-xs whitespace-nowrap">{fmtDate(c.created_at)}</TableCell>
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
                <DialogTitle>Clip {open.id.slice(0, 8)}</DialogTitle>
                <DialogDescription>{open.campaign_name}</DialogDescription>
              </DialogHeader>
              <KV
                items={[
                  ['Asset', <ExtLink key="a" href={open.asset_source_url} />],
                  ['Fichero', <PathCell key="p" path={open.final_path_worker ?? open.file_path} baseUrl={data?.worker_file_base_url} />],
                  ['Tamaño', fmtBytes(open.file_size)],
                  ['QA', fmtDate(open.qa_at)],
                  ['Aprobado para publicar', fmtDate(open.publish_approved_at)],
                  ['Publicado', fmtDate(open.published_at)],
                ]}
              />
              <div className="mb-1 text-xs font-medium">qa_result</div>
              <JsonBlock value={open.qa_result} />
            </>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}
