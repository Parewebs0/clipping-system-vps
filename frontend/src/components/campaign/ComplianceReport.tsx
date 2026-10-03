import { useState } from 'react'
import { toast } from 'sonner'
import { useApprovePublish, useVerifyClip } from '@/api/mutations'
import type { McClip } from '@/api/types'
import { ToneBadge } from '@/components/common/StatusBadge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import type { Tone } from '@/lib/status'

type Check = { rule: string; status: string; expected?: unknown; actual?: unknown; how?: string; detail?: string; platform?: string }

const TONE: Record<string, Tone> = { pass: 'emerald', fail: 'rose', review: 'amber', pending: 'slate', 'n/a': 'zinc' }
const LABEL: Record<string, string> = { pass: 'cumple', fail: 'no cumple', review: 'revisión humana', pending: 'pendiente' }

const show = (v: unknown) => (v == null ? '—' : typeof v === 'string' ? v : JSON.stringify(v))

export function ComplianceCell({ clip, campaignId }: { clip: McClip; campaignId?: number }) {
  const st = clip.compliance_status ?? 'pending'
  const report = (clip.compliance_report ?? {}) as { checks?: Check[]; checked_at?: string; failed?: string[] }
  const checks = report.checks ?? []
  const reviewIdx = checks.map((c, i) => (c.status === 'review' ? i : -1)).filter((i) => i >= 0)
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const approved = Boolean(clip.publish_approved_at)
  const ready = reviewIdx.every((i) => picked.has(i))
  const verify = useVerifyClip(campaignId)
  const approve = useApprovePublish(campaignId)
  const toggle = (i: number) =>
    setPicked((s) => {
      const n = new Set(s)
      if (n.has(i)) n.delete(i)
      else n.add(i)
      return n
    })
  const doApprove = async () => {
    try {
      const r = await approve.mutateAsync({ clipId: clip.id, confirmed_checks: reviewIdx.filter((i) => picked.has(i)) })
      toast.success(r.already_approved ? 'La publicación ya estaba aprobada' : 'Publicación aprobada')
    } catch (e) {
      toast.error('No se pudo aprobar la publicación', { description: e instanceof Error ? e.message : String(e) })
    }
  }
  const rerun = async () => {
    try {
      const r = await verify.mutateAsync(clip.id)
      toast.success(`Verificación: ${LABEL[r.compliance_status] ?? r.compliance_status}`)
    } catch (e) {
      toast.error('No se pudo verificar', { description: e instanceof Error ? e.message : String(e) })
    }
  }
  return (
    <Dialog>
      <DialogTrigger asChild>
        <button type="button" className="cursor-pointer" aria-label="informe de reglas">
          <ToneBadge tone={TONE[st] ?? 'slate'}>
            {LABEL[st] ?? st}
            {report.failed?.length ? ` (${report.failed.length})` : ''}
          </ToneBadge>
        </button>
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] overflow-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>Informe de reglas del clip {clip.id.slice(0, 8)}</DialogTitle>
          <DialogDescription>
            Verificador post-render (#43). Publicar exige «cumple»; los checks de revisión humana se confirman al aprobar el publish.
            {report.checked_at ? ` Verificado ${report.checked_at.slice(0, 16).replace('T', ' ')} UTC.` : ''}
          </DialogDescription>
        </DialogHeader>
        {checks.length === 0 && <div className="text-muted-foreground text-sm">Sin verificar todavía.</div>}
        {checks.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-muted-foreground border-b text-left text-xs">
                <th className="py-1 pr-2">Regla</th>
                <th className="py-1 pr-2">Resultado</th>
                <th className="py-1 pr-2">Esperado</th>
                <th className="py-1 pr-2">Real</th>
                <th className="py-1">Cómo se verifica</th>
              </tr>
            </thead>
            <tbody>
              {checks.map((c, i) => (
                <tr key={i} className="border-b align-top last:border-0">
                  <td className="py-1.5 pr-2 font-mono text-xs">
                    {c.rule}
                    {c.platform ? ` (${c.platform})` : ''}
                  </td>
                  <td className="py-1.5 pr-2">
                    <ToneBadge tone={TONE[c.status] ?? 'slate'}>{LABEL[c.status] ?? c.status}</ToneBadge>
                  </td>
                  <td className="max-w-[16rem] py-1.5 pr-2 font-mono text-xs break-words">{show(c.expected)}</td>
                  <td className="max-w-[16rem] py-1.5 pr-2 font-mono text-xs break-words">
                    {show(c.actual)}
                    {c.detail ? <div className="text-rose-700">{c.detail}</div> : null}
                  </td>
                  <td className="py-1.5 text-xs">{c.how}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {!approved && reviewIdx.length > 0 && (
          <div className="space-y-1">
            <div className="text-sm font-medium">Checklist antes de aprobar la publicación</div>
            {reviewIdx.map((i) => (
              <label key={i} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={picked.has(i)}
                  onChange={() => toggle(i)}
                  aria-label={`checklist ${checks[i].rule} ${i}`}
                />
                <span className="font-mono text-xs">
                  {checks[i].rule}
                  {checks[i].platform ? ` (${checks[i].platform})` : ''}
                </span>
              </label>
            ))}
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={rerun} disabled={verify.isPending}>
            Re-verificar
          </Button>
          <Button size="sm" onClick={doApprove} disabled={approved || approve.isPending || (reviewIdx.length > 0 && !ready)}>
            {approved ? 'Publicación aprobada' : 'Aprobar publicación'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
