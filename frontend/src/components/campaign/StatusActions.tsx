import { useState } from 'react'
import { ArrowRightLeft, ChevronDown } from 'lucide-react'
import { toast } from 'sonner'
import { useChangeStatus, useStatusMachine } from '@/api/mutations'
import type { CampaignDetailCampaign } from '@/api/types'
import { CampaignStatusBadge } from '@/components/common/StatusBadge'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { campaignStatusMeta } from '@/lib/status'

export function StatusActions({ campaign }: { campaign: CampaignDetailCampaign }) {
  const sm = useStatusMachine()
  const change = useChangeStatus(campaign.id)
  const [target, setTarget] = useState<string | null>(null)
  const [reason, setReason] = useState('')
  const targets = sm.data?.transitions[campaign.status] ?? []
  const effect = sm.data?.statuses.find((s) => s.value === target)?.effect

  const confirm = async () => {
    if (!target) return
    try {
      await change.mutateAsync({ status: target, reason: reason.trim() || null })
      toast.success(`Estado cambiado a «${campaignStatusMeta(target).label}»`)
      setTarget(null)
      setReason('')
    } catch (e) {
      toast.error('No se pudo cambiar el estado', { description: e instanceof Error ? e.message : String(e) })
    }
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" disabled={!targets.length}>
            <ArrowRightLeft className="size-4" /> Cambiar estado <ChevronDown className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-72">
          <DropdownMenuLabel className="text-muted-foreground text-xs font-normal">
            Transiciones manuales permitidas desde «{campaignStatusMeta(campaign.status).label}». Los pasos hacia delante los hacen los crons.
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          {targets.map((t) => (
            <DropdownMenuItem key={t} onSelect={() => setTarget(t)}>
              <CampaignStatusBadge status={t} />
              <span className="text-muted-foreground ml-auto font-mono text-xs">{t}</span>
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      <AlertDialog open={!!target} onOpenChange={(o) => !o && setTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>¿Cambiar el estado de la campaña?</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <CampaignStatusBadge status={campaign.status} /> → {target && <CampaignStatusBadge status={target} />}
                </div>
                {effect && <p>{effect}</p>}
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-2">
            <Label htmlFor="status-reason">Motivo (opcional, queda en el historial)</Label>
            <Textarea id="status-reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault()
                void confirm()
              }}
              disabled={change.isPending}
            >
              {change.isPending ? 'Cambiando…' : 'Confirmar'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
