import { Link } from 'react-router-dom'
import { useVideos } from '@/api/queries'
import { ExtLink, PathCell } from '@/components/common/Misc'
import { PageHeader } from '@/components/common/PageHeader'
import { EmptyBlock, ErrorBlock, LoadingBlock } from '@/components/common/States'
import { StatusBadge } from '@/components/common/StatusBadge'
import { Card, CardContent } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { fmtBytes, fmtDate, fmtDuration } from '@/lib/format'

export function VideosPage() {
  const { data, error, isLoading } = useVideos()
  return (
    <>
      <PageHeader eyebrow="Worker" title="Vídeos" description="Assets descargados o transcritos y su ruta en el worker" />
      {isLoading && <LoadingBlock rows={8} />}
      {error && <ErrorBlock error={error} />}
      {data &&
        (data.items.length === 0 ? (
          <EmptyBlock>Todavía no hay vídeos descargados.</EmptyBlock>
        ) : (
          <Card className="py-0">
            <CardContent className="px-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="pl-4">Campaña</TableHead>
                    <TableHead>Origen</TableHead>
                    <TableHead>Estado</TableHead>
                    <TableHead>Ruta local</TableHead>
                    <TableHead className="text-right">Tamaño</TableHead>
                    <TableHead className="text-right">Duración</TableHead>
                    <TableHead className="pr-4">Descargado</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.items.map((v) => (
                    <TableRow key={v.id}>
                      <TableCell className="max-w-48 truncate pl-4">
                        <Link className="hover:underline" to={`/campaigns/${v.campaign_id}`}>
                          {v.campaign_name ?? v.campaign_id}
                        </Link>
                      </TableCell>
                      <TableCell className="max-w-48">
                        <ExtLink href={v.source_url} />
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={v.status} />
                      </TableCell>
                      <TableCell className="max-w-xs">
                        <PathCell path={v.local_path} baseUrl={data.worker_file_base_url} />
                      </TableCell>
                      <TableCell className="text-right text-xs tabular-nums">{fmtBytes(v.file_size)}</TableCell>
                      <TableCell className="text-right text-xs tabular-nums">{fmtDuration(v.duration_seconds)}</TableCell>
                      <TableCell className="pr-4 text-xs whitespace-nowrap">{fmtDate(v.downloaded_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        ))}
    </>
  )
}
