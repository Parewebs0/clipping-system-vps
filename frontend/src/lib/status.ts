import type { AssetStatus, CampaignStatus, ClipQAStatus, ClipStatus, JobStatus } from '@/api/types'

export type Tone = 'slate' | 'amber' | 'sky' | 'emerald' | 'rose' | 'violet' | 'zinc'

export const TONE_CLASS: Record<Tone, string> = {
  slate: 'bg-slate-100 text-slate-700 border-slate-200',
  amber: 'bg-amber-50 text-amber-800 border-amber-200',
  sky: 'bg-sky-50 text-sky-800 border-sky-200',
  emerald: 'bg-emerald-50 text-emerald-800 border-emerald-200',
  rose: 'bg-rose-50 text-rose-800 border-rose-200',
  violet: 'bg-violet-50 text-violet-800 border-violet-200',
  zinc: 'bg-zinc-100 text-zinc-500 border-zinc-200',
}

type Meta = { label: string; tone: Tone; help?: string }

// Order = pipeline order (left → right). Mirrors app/models/campaign.py.
export const CAMPAIGN_STATUS: Record<CampaignStatus, Meta> = {
  discovered: { label: 'Descubierta', tone: 'slate', help: 'Paso 1: upsert mínimo; la recoge el brief-reader (3a).' },
  briefed: { label: 'Brief leído', tone: 'amber', help: 'Paso 3a: reglas y enlaces extraídos; la recoge el resolver (3b).' },
  assets_resolved: { label: 'Assets resueltos', tone: 'sky', help: 'Paso 3b: assets creados; la recoge el scorer (3c).' },
  scored: { label: 'Puntuada', tone: 'emerald', help: 'Paso 3c: score ≥ umbral; download_enqueue encola descargas.' },
  blocked_no_assets: { label: 'Bloqueada', tone: 'rose', help: '3c: score bajo, sin assets reales o host no soportado.' },
  failed_brief: { label: 'Fallo brief', tone: 'rose', help: '3a: brief ilegible o sin materiales.' },
  failed_resolve: { label: 'Fallo resolve', tone: 'rose', help: '3b: sin vídeos ingeribles / acceso denegado.' },
}

export const CAMPAIGN_STATUS_ORDER = Object.keys(CAMPAIGN_STATUS) as CampaignStatus[]

export function campaignStatusMeta(s: string): Meta {
  return (CAMPAIGN_STATUS as Record<string, Meta>)[s] ?? { label: s, tone: 'slate' }
}

export const ASSET_STATUS: Record<AssetStatus, Meta> = {
  pending: { label: 'pending', tone: 'slate' },
  downloaded: { label: 'downloaded', tone: 'sky' },
  transcribed: { label: 'transcribed', tone: 'emerald' },
  failed: { label: 'failed', tone: 'rose' },
}

export const JOB_STATUS: Record<JobStatus, Meta> = {
  pending: { label: 'pending', tone: 'slate' },
  assigned: { label: 'assigned', tone: 'violet' },
  processing: { label: 'processing', tone: 'amber' },
  completed: { label: 'completed', tone: 'emerald' },
  failed: { label: 'failed', tone: 'rose' },
  cancelled: { label: 'cancelled', tone: 'zinc' },
}

export const CLIP_QA_STATUS: Record<ClipQAStatus, Meta> = {
  pending: { label: 'QA pending', tone: 'slate' },
  pass: { label: 'QA pass', tone: 'emerald' },
  fail: { label: 'QA fail', tone: 'rose' },
  review: { label: 'QA review', tone: 'amber' },
}

export const CLIP_STATUS: Record<ClipStatus, Meta> = {
  created: { label: 'created', tone: 'slate' },
  approved: { label: 'approved', tone: 'emerald' },
  rejected: { label: 'rejected', tone: 'rose' },
  review: { label: 'review', tone: 'amber' },
  published: { label: 'published', tone: 'violet' },
}

export function anyStatusMeta(s?: string | null): Meta {
  if (!s) return { label: '—', tone: 'zinc' }
  const all: Record<string, Meta> = {
    ...JOB_STATUS,
    ...ASSET_STATUS,
    ...CLIP_STATUS,
    ...(CAMPAIGN_STATUS as Record<string, Meta>),
    uploaded: { label: 'uploaded', tone: 'violet' },
    archived: { label: 'archived', tone: 'zinc' },
    pending_upload: { label: 'pending upload', tone: 'amber' },
  }
  return all[s] ?? { label: s, tone: 'slate' }
}

// Hints for briefing_error.kind / resolve_error.kind actually written by the
// ticks (scripts/brief_reader_tick.py, scripts/drive_resolver_tick.py).
export const ERROR_HINTS: Record<string, string> = {
  no_materials: 'El payload de Whop no trae brief ni reference_materials. Revisa la campaña en Whop.',
  llm_timeout: 'El LLM tardó demasiado. Devuelve la campaña a «Descubierta» para reintentar.',
  gog: 'Fallo de gog (Google Drive). El resolver lo reintenta solo; si persiste con invalid_grant, re-autoriza gog.',
  gog_error: 'Fallo de gog (Google Drive). El resolver lo reintenta solo.',
  social_only: 'Solo hay enlaces sociales o de referencia (IG, TikTok, perfiles): nada descargable. Terminal, no se reintenta.',
  unsupported_source: 'Los enlaces apuntan a hosts no soportados (ni Drive, ni fichero de Dropbox, ni vídeo directo).',
  no_videos: 'La carpeta o el enlace no contiene vídeos ingeribles.',
  dropbox_folder_needs_list: 'Carpeta de Dropbox: todavía no se listan. Hacen falta enlaces a ficheros sueltos.',
  legacy_string: 'Error antiguo sin clasificar (formato previo a pipeline v2).',
  other: 'Causa no clasificada. Revisa los logs del cron en /home/jarvis/clipping-cron/logs/.',
}

export function errorHint(kind?: string | null): string {
  return ERROR_HINTS[kind ?? 'other'] ?? ERROR_HINTS.other
}
