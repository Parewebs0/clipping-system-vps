import type { McAsset, McClip, McJob, Pipeline } from '@/api/types'
import { ExtLink, Mono, PathCell } from '@/components/common/Misc'
import { EmptyBlock } from '@/components/common/States'
import { StatusBadge, ToneBadge } from '@/components/common/StatusBadge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { fmtBytes, fmtDate, fmtDuration } from '@/lib/format'
import { CLIP_QA_STATUS } from '@/lib/status'
import { ComplianceCell } from './ComplianceReport'

export function AssetsTable({ rows, baseUrl }: { rows: McAsset[]; baseUrl?: string | null }) {
  if (!rows.length) return <EmptyBlock>Sin assets. El resolver (3b) los crea a partir de los enlaces del brief.</EmptyBlock>
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Origen</TableHead>
          <TableHead>Tipo</TableHead>
          <TableHead>Estado</TableHead>
          <TableHead>Ruta local</TableHead>
          <TableHead className="text-right">Tamaño</TableHead>
          <TableHead className="text-right">Duración</TableHead>
          <TableHead>Descargado</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((a) => {
          const errKind = a.extra_metadata?.last_error_kind as string | undefined
          return (
            <TableRow key={a.id}>
              <TableCell className="max-w-xs">
                <ExtLink href={a.source_url} />
                <Mono className="text-muted-foreground block">{a.id.slice(0, 8)}</Mono>
              </TableCell>
              <TableCell className="text-xs">{a.asset_type}</TableCell>
              <TableCell>
                <div className="flex flex-col items-start gap-1">
                  <StatusBadge status={a.status} />
                  {errKind && (
                    <ToneBadge tone="rose" className="font-mono text-[10px]" title={String(a.extra_metadata?.last_error_detail ?? '')}>
                      {errKind}
                    </ToneBadge>
                  )}
                </div>
              </TableCell>
              <TableCell className="max-w-xs">
                <PathCell path={a.local_path} baseUrl={baseUrl} />
              </TableCell>
              <TableCell className="text-right text-xs tabular-nums">{fmtBytes(a.file_size)}</TableCell>
              <TableCell className="text-right text-xs tabular-nums">{fmtDuration(a.duration_seconds)}</TableCell>
              <TableCell className="text-xs whitespace-nowrap">{fmtDate(a.downloaded_at)}</TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}

export function ClipsTable({ rows, baseUrl }: { rows: McClip[]; baseUrl?: string | null }) {
  if (!rows.length) return <EmptyBlock>Sin clips todavía.</EmptyBlock>
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Clip</TableHead>
          <TableHead>QA</TableHead>
          <TableHead>Reglas</TableHead>
          <TableHead>Estado</TableHead>
          <TableHead>Ubicación</TableHead>
          <TableHead>Fichero</TableHead>
          <TableHead className="text-right">Duración</TableHead>
          <TableHead>Creado</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((c) => (
          <TableRow key={c.id}>
            <TableCell>
              <Mono>{c.id.slice(0, 8)}</Mono>
            </TableCell>
            <TableCell>
              <ToneBadge tone={CLIP_QA_STATUS[c.qa_status].tone}>{CLIP_QA_STATUS[c.qa_status].label}</ToneBadge>
            </TableCell>
            <TableCell>
              <ComplianceCell clip={c} />
            </TableCell>
            <TableCell>
              <StatusBadge status={c.status} />
            </TableCell>
            <TableCell>{c.location ? <StatusBadge status={c.location} /> : <span className="text-muted-foreground text-xs">—</span>}</TableCell>
            <TableCell className="max-w-xs">
              <PathCell path={c.final_path_worker ?? c.file_path} baseUrl={baseUrl} />
            </TableCell>
            <TableCell className="text-right text-xs tabular-nums">{fmtDuration(c.duration_seconds)}</TableCell>
            <TableCell className="text-xs whitespace-nowrap">{fmtDate(c.created_at)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

export function ActiveJobsTable({ rows }: { rows: McJob[] }) {
  if (!rows.length) return <EmptyBlock>Sin jobs activos (pending / assigned / processing).</EmptyBlock>
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Job</TableHead>
          <TableHead>Tipo</TableHead>
          <TableHead>Estado</TableHead>
          <TableHead className="text-right">Intentos</TableHead>
          <TableHead>Worker</TableHead>
          <TableHead>Creado</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((j) => (
          <TableRow key={j.id}>
            <TableCell>
              <Mono>{j.id.slice(0, 8)}</Mono>
            </TableCell>
            <TableCell className="font-mono text-xs">{j.job_type}</TableCell>
            <TableCell>
              <StatusBadge status={j.status} />
            </TableCell>
            <TableCell className="text-right text-xs tabular-nums">
              {j.attempts}/{j.max_attempts}
            </TableCell>
            <TableCell className="text-xs">{j.worker_id ?? '—'}</TableCell>
            <TableCell className="text-xs whitespace-nowrap">{fmtDate(j.created_at)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

const STAGES = ['download', 'transcribe', 'render', 'qa'] as const

export function PipelineTable({ data }: { data?: Pipeline }) {
  if (!data || !data.assets.length) return <EmptyBlock>Ningún asset tiene jobs todavía.</EmptyBlock>
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Asset</TableHead>
          <TableHead>Estado asset</TableHead>
          {STAGES.map((s) => (
            <TableHead key={s}>{s}</TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {data.assets.map((a) => (
          <TableRow key={a.asset_id}>
            <TableCell className="max-w-xs">
              <ExtLink href={a.source_url} />
            </TableCell>
            <TableCell>
              <StatusBadge status={a.asset_status} />
            </TableCell>
            {STAGES.map((s) => {
              const st = a.stages[s]
              return (
                <TableCell key={s} title={st?.error_message ?? undefined}>
                  {st ? <StatusBadge status={st.status} /> : <span className="text-muted-foreground text-xs">—</span>}
                </TableCell>
              )
            })}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
