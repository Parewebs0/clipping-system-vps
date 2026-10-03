import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { useDeleteCampaign } from '@/api/mutations'
import type { CampaignDetailCampaign } from '@/api/types'
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
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

/** Only rendered for archived campaigns (the API refuses anything else). */
export function DeleteCampaignButton({ campaign }: { campaign: CampaignDetailCampaign }) {
  const del = useDeleteCampaign(campaign.id)
  const navigate = useNavigate()
  const [typed, setTyped] = useState('')
  if (campaign.status !== 'archived') return null
  return (
    <AlertDialog onOpenChange={(o) => !o && setTyped('')}>
      <AlertDialogTrigger asChild>
        <Button variant="destructive" size="sm">
          <Trash2 className="size-4" /> Borrar
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Borrar definitivamente</AlertDialogTitle>
          <AlertDialogDescription>
            Se borrarán la campaña y, en cascada, sus assets, candidatos, clips y publicaciones. No se puede deshacer.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="space-y-2">
          <Label htmlFor="confirm-name">
            Escribe <span className="font-mono">{campaign.name}</span> para confirmar
          </Label>
          <Input id="confirm-name" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancelar</AlertDialogCancel>
          <AlertDialogAction
            className="bg-destructive hover:bg-destructive/90 text-white"
            disabled={typed !== campaign.name || del.isPending}
            onClick={async (e) => {
              e.preventDefault()
              try {
                await del.mutateAsync()
                toast.success('Campaña borrada')
                navigate('/campaigns')
              } catch (err) {
                toast.error('No se pudo borrar', { description: err instanceof Error ? err.message : String(err) })
              }
            }}
          >
            Borrar
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
