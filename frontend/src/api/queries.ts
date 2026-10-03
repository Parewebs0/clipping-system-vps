import { useQuery } from '@tanstack/react-query'
import { api, qs } from './client'
import type {
  CampaignDetail,
  CampaignList,
  CampaignRules,
  CampaignRuleset,
  Candidates,
  Clips,
  JobRecent,
  Overview,
  Pipeline,
  Videos,
} from './types'

const POLL_MS = 10_000

export const keys = {
  overview: ['overview'] as const,
  campaigns: ['campaigns'] as const,
  campaign: (id: number) => ['campaign', id] as const,
  rules: (id: number) => ['campaign', id, 'rules'] as const,
  ruleset: (id: number) => ['campaign', id, 'ruleset'] as const,
  pipeline: (id: number) => ['campaign', id, 'pipeline'] as const,
  jobs: (f: { job_type?: string; status?: string }) => ['jobs', f] as const,
  videos: ['videos'] as const,
  clips: (f: { qa_status?: string; campaign_id?: number }) => ['clips', f] as const,
  candidates: (f: { status?: string; campaign_id?: number }) => ['candidates', f] as const,
}

export const useOverview = () =>
  useQuery({
    queryKey: keys.overview,
    queryFn: () => api<Overview>('/mission-control/overview'),
    refetchInterval: POLL_MS,
  })

export const useCampaigns = () =>
  useQuery({
    queryKey: keys.campaigns,
    queryFn: () => api<CampaignList>('/mission-control/campaigns?limit=500'),
  })

export const useCampaign = (id: number) =>
  useQuery({
    queryKey: keys.campaign(id),
    queryFn: () => api<CampaignDetail>(`/mission-control/campaigns/${id}`),
  })

export const useCampaignRules = (id: number, enabled = true) =>
  useQuery({
    queryKey: keys.rules(id),
    queryFn: () => api<CampaignRules>(`/mission-control/campaigns/${id}/rules`),
    enabled,
  })

export const useCampaignRuleset = (id: number, enabled = true) =>
  useQuery({
    queryKey: keys.ruleset(id),
    queryFn: () => api<CampaignRuleset>(`/campaigns/${id}/ruleset`),
    enabled,
  })

export const usePipeline = (id: number, enabled = true) =>
  useQuery({
    queryKey: keys.pipeline(id),
    queryFn: () => api<Pipeline>(`/mission-control/pipeline/${id}`),
    enabled,
  })

export const useJobs = (f: { job_type?: string; status?: string }) =>
  useQuery({
    queryKey: keys.jobs(f),
    queryFn: () => api<JobRecent>(`/mission-control/jobs/recent${qs({ limit: 200, ...f })}`),
    refetchInterval: POLL_MS,
  })

export const useVideos = () =>
  useQuery({
    queryKey: keys.videos,
    queryFn: () => api<Videos>('/mission-control/videos?limit=500'),
  })

export const useClips = (f: { qa_status?: string; campaign_id?: number }) =>
  useQuery({
    queryKey: keys.clips(f),
    queryFn: () => api<Clips>(`/mission-control/clips${qs({ limit: 500, ...f })}`),
  })

export const useCandidates = (f: { status?: string; campaign_id?: number }) =>
  useQuery({
    queryKey: keys.candidates(f),
    queryFn: () => api<Candidates>(`/mission-control/candidates${qs({ limit: 500, ...f })}`),
  })
