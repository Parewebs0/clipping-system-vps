import { useState } from 'react'
import { useCampaigns } from '@/api/queries'
import { CandidatesPanel } from '@/components/candidates/CandidatesPanel'
import { PageHeader } from '@/components/common/PageHeader'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

export function CandidatesPage() {
  const [campaign, setCampaign] = useState('all')
  const campaigns = useCampaigns()
  return (
    <>
      <PageHeader
        eyebrow="Paso 14"
        title="Candidatos"
        description="Clips propuestos por el decider. Aprobar crea el job de render (no publica); rechazar lo descarta."
        actions={
          <Select value={campaign} onValueChange={setCampaign}>
            <SelectTrigger className="w-64" aria-label="Campaña">
              <SelectValue placeholder="Campaña" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Todas las campañas</SelectItem>
              {campaigns.data?.items.map((c) => (
                <SelectItem key={c.id} value={String(c.id)}>
                  {c.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
      />
      <CandidatesPanel key={campaign} campaignId={campaign === 'all' ? undefined : Number(campaign)} />
    </>
  )
}
