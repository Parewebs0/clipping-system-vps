import { useState } from 'react'
import { Check, X } from 'lucide-react'
import { toast } from 'sonner'
import { useApproveCandidate, useRejectCandidate } from '@/api/mutations'
import type { CandidateItem } from '@/api/types'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { approveOutcome, canApprove, canReject, fmtTimecode, rejectSchema } from '@/lib/candidates'

function errMsg(err: unknown) {
  return err instanceof Error ? err.message : String(err)
}

function Window({ c }: { c: CandidateItem }) {
  return (
    <span className="font-mono">
      {fmtTimecode(c.start_time)}–{fmtTimecode(c.end_time)} ({c.duration_seconds.toFixed(1)}s)
    </span>
  )
}

export function ApproveCandidateButton({ c }: { c: CandidateItem }) {
  const approve = useApproveCandidate()
  const [open, setOpen] = useState(false)
  if (!canApprove(c)) return null
  return (
    <AlertDialog open={open} onOpenChange={setOpen}>
      <AlertDialogTrigger asChild>
        <Button size="sm">
          <Check className="size-4" /> Aprobar
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Aprobar candidato</AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-2">
              <p>
                <span className="text-foreground font-medium">{c.title || 'Sin título'}</span> · <Window c={c} />
              </p>
              <p>
                El backend re-valida la ventana contra las reglas actuales de la campaña. Si pasa, se crea un <b>job de render</b> que
                cogerá el worker Windows. No publica nada: la publicación sigue necesitando su aprobación aparte.
              </p>
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancelar</AlertDialogCancel>
          <AlertDialogAction
            disabled={approve.isPending}
            onClick={async (e) => {
              e.preventDefault()
              try {
                const r = await approve.mutateAsync({ id: c.id, campaignId: c.campaign_id })
                const o = approveOutcome(r)
                if (o.ok) toast.success(o.title, { description: o.description })
                else toast.warning(o.title, { description: o.description })
                setOpen(false)
              } catch (err) {
                toast.error('No se pudo aprobar', { description: errMsg(err) })
              }
            }}
          >
            Aprobar y crear render
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

export function RejectCandidateButton({ c }: { c: CandidateItem }) {
  const reject = useRejectCandidate()
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const parsed = rejectSchema.safeParse({ reason })
  const error = parsed.success ? null : parsed.error.issues[0]?.message
  if (!canReject(c)) return null
  return (
    <AlertDialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o)
        if (!o) setReason('')
      }}
    >
      <AlertDialogTrigger asChild>
        <Button size="sm" variant="outline">
          <X className="size-4" /> Rechazar
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Rechazar candidato</AlertDialogTitle>
          <AlertDialogDescription>
            {c.title || 'Sin título'} · <Window c={c} />. Queda como rechazado y no se renderiza.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="space-y-2">
          <Label htmlFor={`reason-${c.id}`}>Motivo (opcional)</Label>
          <Textarea
            id={`reason-${c.id}`}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="p. ej. el gancho no se entiende sin contexto"
            aria-invalid={!!error}
          />
          {error && <p className="text-destructive text-xs">{error}</p>}
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancelar</AlertDialogCancel>
          <AlertDialogAction
            className="bg-destructive hover:bg-destructive/90 text-white"
            disabled={!parsed.success || reject.isPending}
            onClick={async (e) => {
              e.preventDefault()
              if (!parsed.success) return
              try {
                await reject.mutateAsync({ id: c.id, campaignId: c.campaign_id, reason: parsed.data.reason })
                toast.success('Candidato rechazado')
                setOpen(false)
              } catch (err) {
                toast.error('No se pudo rechazar', { description: errMsg(err) })
              }
            }}
          >
            Rechazar
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
