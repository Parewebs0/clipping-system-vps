import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './client'
import { keys } from './queries'
import type {
  CampaignCreate,
  CampaignOut,
  CampaignStatusChange,
  CampaignUpdate,
  CandidateApproveOut,
  CandidateOut,
  CampaignRuleset,
  RulesConfirmIn,
  StatusMachine,
} from './types'

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

// --- Candidates (issue #17): write token required when API_WRITE_TOKEN is set.
function useInvalidateCandidates() {
  const qc = useQueryClient()
  return (campaignId: number) => {
    qc.invalidateQueries({ queryKey: ['candidates'] })
    qc.invalidateQueries({ queryKey: ['campaign', campaignId] })
    qc.invalidateQueries({ queryKey: keys.overview })
  }
}

export function useApproveCandidate() {
  const invalidate = useInvalidateCandidates()
  return useMutation({
    mutationFn: ({ id }: { id: string; campaignId: number }) =>
      api<CandidateApproveOut>(`/candidates/${id}/approve`, { method: 'POST' }),
    onSuccess: (_d, v) => invalidate(v.campaignId),
  })
}

export function useRejectCandidate() {
  const invalidate = useInvalidateCandidates()
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; campaignId: number; reason?: string }) =>
      api<CandidateOut>(`/candidates/${id}/reject`, { method: 'POST', body: JSON.stringify({ reason: reason ?? null }) }),
    onSuccess: (_d, v) => invalidate(v.campaignId),
  })
}

// --- #37 rules gate: confirm human requirements (write token).
export function useConfirmRules(id: number) {
  const invalidate = useInvalidateCampaign()
  return useMutation({
    mutationFn: (body: RulesConfirmIn) =>
      api<CampaignRuleset>(`/campaigns/${id}/rules/confirm`, { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => invalidate(id),
  })
}

// --- #43 post-render rules verifier: re-run (write token).
export function useVerifyClip(campaignId?: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (clipId: string) => api<{ id: string; compliance_status: string }>(`/clips/${clipId}/verify`, { method: 'POST' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['clips'] })
      if (campaignId != null) qc.invalidateQueries({ queryKey: ['campaign', campaignId] })
      else qc.invalidateQueries({ queryKey: ['campaign'] })
    },
  })
}
