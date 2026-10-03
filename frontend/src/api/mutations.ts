import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './client'
import { keys } from './queries'
import type { CampaignCreate, CampaignOut, CampaignStatusChange, CampaignUpdate, StatusMachine } from './types'

export const useStatusMachine = () =>
  useQuery({
    queryKey: ['status-machine'],
    queryFn: () => api<StatusMachine>('/campaigns/status-machine'),
    staleTime: Infinity,
  })

function useInvalidateCampaign() {
  const qc = useQueryClient()
  return (id?: number) => {
    qc.invalidateQueries({ queryKey: keys.campaigns })
    qc.invalidateQueries({ queryKey: keys.overview })
    if (id != null) qc.invalidateQueries({ queryKey: ['campaign', id] })
  }
}

export function useUpdateCampaign(id: number) {
  const invalidate = useInvalidateCampaign()
  return useMutation({
    mutationFn: (body: CampaignUpdate) => api<CampaignOut>(`/campaigns/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    onSuccess: () => invalidate(id),
  })
}

export function useChangeStatus(id: number) {
  const invalidate = useInvalidateCampaign()
  return useMutation({
    mutationFn: (body: CampaignStatusChange) => api<CampaignOut>(`/campaigns/${id}/status`, { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => invalidate(id),
  })
}

export function useDeleteCampaign(id: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api<void>(`/campaigns/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.removeQueries({ queryKey: ['campaign', id] })
      qc.invalidateQueries({ queryKey: keys.campaigns })
      qc.invalidateQueries({ queryKey: keys.overview })
    },
  })
}

export function useCreateCampaign() {
  const invalidate = useInvalidateCampaign()
  return useMutation({
    mutationFn: (body: CampaignCreate) => api<CampaignOut>('/campaigns', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => invalidate(),
  })
}
