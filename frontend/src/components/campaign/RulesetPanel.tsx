import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { getToken } from '@/api/client'
import { useConfirmRules, useUploadLogo } from '@/api/mutations'
import { useCampaignRuleset } from '@/api/queries'
import type { RuleBlocker } from '@/api/types'
import { ErrorBlock, LoadingBlock } from '@/components/common/States'
import { CampaignStatusBadge, ToneBadge } from '@/components/common/StatusBadge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Textarea } from '@/components/ui/textarea'
import type { Tone } from '@/lib/status'

type Dict = Record<string, unknown>
type Evidence = { quote: string; source?: string; verified?: boolean | null }

const ENF_TONE: Record<string, Tone> = { auto: 'emerald', human: 'amber', unsupported: 'rose' }
const ENF_LABEL: Record<string, string> = { auto: 'auto', human: 'humano', unsupported: 'no soportada' }

// Order and Spanish labels of the RuleSet v2 sections (#33).
const SECTIONS: [string, string][] = [
  ['duration', 'Duración'],
  ['aspect', 'Aspecto'],
  ['language', 'Idioma'],
  ['captions', 'Subtítulos'],
  ['on_screen_text', 'Texto en pantalla'],
  ['logo', 'Logo'],
  ['audio', 'Audio / música'],
  ['hook', 'Gancho'],
  ['edit', 'Edición'],
  ['source', 'Origen'],
  ['copy', 'Copy (caption, menciones, hashtags, FTC)'],
  ['prohibitions', 'Prohibiciones'],
  ['pre_approval', 'Pre-aprobación'],
]
const META_KEYS = new Set(['required', 'evidence', 'enforcement', 'scope', 'note'])

function isEmpty(v: unknown): boolean {
  if (v == null || v === false || v === '') return true
  if (Array.isArray(v)) return v.length === 0
  if (typeof v === 'object') return Object.values(v as Dict).every(isEmpty)
  return false
}

function summary(rule: Dict): string {
  const out: string[] = []
  for (const [k, v] of Object.entries(rule)) {
    if (META_KEYS.has(k) || isEmpty(v)) continue
    if (typeof v === 'object' && !Array.isArray(v)) {
      const inner = Object.entries(v as Dict)
        .filter(([, iv]) => !isEmpty(iv))
        .map(([ik, iv]) => `${ik}=${Array.isArray(iv) ? iv.join(' ') : typeof iv === 'object' ? JSON.stringify(iv) : String(iv)}`)
      if (inner.length) out.push(`${k}{${inner.join(', ')}}`)
    } else {
      out.push(`${k}=${Array.isArray(v) ? v.join(' ') : String(v)}`)
    }
  }
  return out.join(' · ')
}

function Quotes({ ev }: { ev?: Evidence[] }) {
  if (!ev?.length) return null
  return (
    <ul className="mt-1 space-y-0.5">
      {ev.slice(0, 3).map((e, i) => (
        <li key={i} className="text-muted-foreground text-xs italic">
          «{e.quote}» <span className="not-italic">({e.source}{e.verified ? ', verificada' : e.verified === false ? ', NO verificada' : ''})</span>
        </li>
      ))}
    </ul>
  )
}

function EnfBadge({ enf }: { enf?: string }) {
  const e = enf ?? 'auto'
  return <ToneBadge tone={ENF_TONE[e] ?? 'slate'}>{ENF_LABEL[e] ?? e}</ToneBadge>
}

function BlockerRow({ b, checked, onToggle }: { b: RuleBlocker; checked: boolean; onToggle: () => void }) {
  const selectable = !b.confirmed
  return (
    <li className="flex items-start gap-3 border-b py-2 last:border-0">
      <input type="checkbox" className="mt-1" disabled={!selectable} checked={b.confirmed || checked} onChange={onToggle} aria-label={`confirmar ${b.key}`} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <ToneBadge tone={b.confirmed ? 'emerald' : b.kind === 'unsupported' ? 'rose' : 'amber'}>
            {b.confirmed
              ? b.confirmation?.type === 'waiver'
                ? 'dispensada'
                : 'confirmado'
              : b.kind === 'unsupported'
                ? 'no soportada'
                : 'pendiente'}
          </ToneBadge>
          {b.rule && <span className="font-mono text-xs">{b.rule}</span>}
          <span>{b.text}</span>
        </div>
        {b.reason && <div className="text-muted-foreground text-xs">Motivo: {b.reason}</div>}
        {b.evidence?.map((q, i) => (
          <div key={i} className="text-muted-foreground text-xs italic">
            «{q}»
          </div>
        ))}
        {b.confirmation && (
          <div className="text-xs text-emerald-700">
            Confirmado por {String(b.confirmation.by)} el {String(b.confirmation.at).slice(0, 16).replace('T', ' ')} UTC
            {b.confirmation.note ? ` — ${String(b.confirmation.note)}` : ''}
          </div>
        )}
      </div>
    </li>
  )
}

function LogoPreview({ url }: { url: string }) {
  const [src, setSrc] = useState<string | null>(null)
  useEffect(() => {
    let dead = false
    let revoke: string | null = null
    const token = getToken()
    fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error('logo'))))
      .then((b) => {
        if (dead) return
        revoke = URL.createObjectURL(b)
        setSrc(revoke)
      })
      .catch(() => {
        if (!dead) setSrc(null)
      })
    return () => {
      dead = true
      if (revoke) URL.revokeObjectURL(revoke)
    }
  }, [url])
  if (!src) return null
  return <img src={src} alt="logo de la campaña" className="h-16 w-auto rounded border" />
}

export function RulesetPanel({ campaignId }: { campaignId: number }) {
  const { data, error, isLoading } = useCampaignRuleset(campaignId)
  const confirm = useConfirmRules(campaignId)
  const upload = useUploadLogo(campaignId)
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [note, setNote] = useState('')
  if (isLoading) return <LoadingBlock />
  if (error) return <ErrorBlock error={error} />
  if (!data) return null
  const rs = (data.ruleset ?? null) as Dict | null
  const blockers = data.blockers ?? []
  const pendingHuman = blockers.filter((b) => b.kind === 'human' && !b.confirmed)
  const unsupported = blockers.filter((b) => b.kind === 'unsupported' && !b.confirmed)
  const humanKeys = new Set(pendingHuman.map((b) => b.key))
  const selHuman = [...sel].filter((k) => humanKeys.has(k))
  const selUnsupported = [...sel].filter((k) => !humanKeys.has(k))
  const toggle = (k: string) =>
    setSel((s) => {
      const n = new Set(s)
      if (n.has(k)) n.delete(k)
      else n.add(k)
      return n
    })
  const submit = async (keys: string[], waive = false) => {
    try {
      const r = await confirm.mutateAsync({ keys, note: note.trim() || null, waive_unsupported: waive })
      setSel(new Set())
      setNote('')
      toast.success(r.pending_count === 0 ? `Reglas confirmadas: campaña en «${r.status}»` : `Confirmado; quedan ${r.pending_count} pendientes`)
    } catch (e) {
      toast.error('No se pudo confirmar', { description: e instanceof Error ? e.message : String(e) })
    }
  }
  const coverage = (rs?.coverage ?? {}) as Dict
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2">
            Trabajabilidad (gate de reglas) <CampaignStatusBadge status={data.status} />
            <ToneBadge tone={data.pending_count ? 'amber' : 'emerald'}>{data.pending_count} pendiente(s)</ToneBadge>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {blockers.length === 0 && <div className="text-muted-foreground text-sm">Sin bloqueantes: la campaña es trabajable de forma automática.</div>}
          {unsupported.length > 0 && (
            <div className="rounded-md border border-rose-200 bg-rose-50 p-2 text-sm text-rose-800">
              Hay {unsupported.length} regla(s) no soportada(s): el pipeline no puede cumplirlas. Aparca o archiva la campaña, o dispénsala con una nota si sabes
              que se cumple igualmente.
            </div>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <label className="text-sm">
              Logo de la campaña
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp,image/svg+xml,.png,.jpg,.jpeg,.webp,.svg"
                className="mt-1 block text-xs"
                aria-label="subir logo"
                disabled={upload.isPending}
                onChange={async (e) => {
                  const f = e.target.files?.[0]
                  e.target.value = ''
                  if (!f) return
                  const body = new FormData()
                  body.append('file', f)
                  try {
                    await upload.mutateAsync(body)
                    toast.success('Logo guardado')
                  } catch (err) {
                    toast.error('No se pudo subir el logo', { description: err instanceof Error ? err.message : String(err) })
                  }
                }}
              />
            </label>
            {data.logo_url ? <LogoPreview url={data.logo_url} /> : null}
          </div>
          <ul>
            {blockers.map((b) => (
              <BlockerRow key={b.key} b={b} checked={sel.has(b.key)} onToggle={() => toggle(b.key)} />
            ))}
          </ul>
          {(pendingHuman.length > 0 || unsupported.length > 0) && (
            <div className="space-y-2">
              <Textarea placeholder="Nota (opcional): p. ej. «bio actualizada en @cuenta»" value={note} onChange={(e) => setNote(e.target.value)} />
              <div className="flex flex-wrap gap-2">
                <Button size="sm" disabled={selHuman.length === 0 || confirm.isPending} onClick={() => submit(selHuman)}>
                  Confirmar seleccionados ({selHuman.length})
                </Button>
                <Button size="sm" variant="outline" disabled={pendingHuman.length === 0 || confirm.isPending} onClick={() => submit(pendingHuman.map((b) => b.key))}>
                  Confirmar todos los requisitos humanos ({pendingHuman.length})
                </Button>
                {unsupported.length > 0 && (
                  <Button
                    size="sm"
                    variant="destructive"
                    disabled={selUnsupported.length === 0 || !note.trim() || confirm.isPending}
                    onClick={() => submit(selUnsupported, true)}
                    title="Requiere nota explicando por qué se cumple"
                  >
                    Dispensar no soportadas seleccionadas ({selUnsupported.length})
                  </Button>
                )}
              </div>
            </div>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>
            Reglas {rs ? `(RuleSet v${String(rs.version)})` : ''}
            {rs && (
              <span className="text-muted-foreground ml-2 text-xs font-normal">
                citas verificadas {String(coverage.evidence_verified ?? 0)}/{String(coverage.evidence_total ?? 0)} · líneas normativas {String(coverage.normative_lines ?? 0)} · sin cubrir{' '}
                {Array.isArray(coverage.uncovered) ? coverage.uncovered.length : 0}
              </span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {!rs && <div className="text-muted-foreground text-sm">Sin RuleSet v2 (pendiente de re-lectura).</div>}
          {rs && (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted-foreground border-b text-left text-xs">
                  <th className="py-1 pr-2">Regla</th>
                  <th className="py-1 pr-2">Exigida</th>
                  <th className="py-1 pr-2">Enforcement</th>
                  <th className="py-1">Valor y cita literal</th>
                </tr>
              </thead>
              <tbody>
                {SECTIONS.map(([k, label]) => {
                  const r = (rs[k] ?? {}) as Dict
                  const req = Boolean(r.required) || !isEmpty(summary(r))
                  return (
                    <tr key={k} className="border-b align-top last:border-0">
                      <td className="py-1.5 pr-2 font-medium">{label}</td>
                      <td className="py-1.5 pr-2">{r.required ? 'sí' : req ? 'parcial' : 'no'}</td>
                      <td className="py-1.5 pr-2">
                        <EnfBadge enf={r.enforcement as string} /> <span className="text-muted-foreground text-xs">{String(r.scope ?? '')}</span>
                      </td>
                      <td className="py-1.5">
                        <div className="font-mono text-xs break-words">{summary(r) || '—'}</div>
                        {r.note ? <div className="text-xs text-amber-700">{String(r.note)}</div> : null}
                        <Quotes ev={r.evidence as Evidence[]} />
                      </td>
                    </tr>
                  )
                })}
                {(['account_requirements', 'manual_checks'] as const).map((k) =>
                  ((rs[k] ?? []) as Dict[]).map((r, i) => (
                    <tr key={`${k}-${i}`} className="border-b align-top last:border-0">
                      <td className="py-1.5 pr-2 font-medium">{k === 'account_requirements' ? `Cuenta (${String(r.kind)})` : 'Check manual'}</td>
                      <td className="py-1.5 pr-2">sí</td>
                      <td className="py-1.5 pr-2">
                        <EnfBadge enf={r.enforcement as string} /> <span className="text-muted-foreground text-xs">{String(r.scope ?? '')}</span>
                      </td>
                      <td className="py-1.5">
                        <div className="text-xs">{String(r.text)}</div>
                        <Quotes ev={r.evidence as Evidence[]} />
                      </td>
                    </tr>
                  )),
                )}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
