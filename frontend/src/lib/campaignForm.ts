import { z } from 'zod'
import type { CampaignDetailCampaign, CampaignSpecPatch, CampaignUpdate } from '@/api/types'

// Mirrors CampaignUpdate / CampaignSpecPatch validation in app/schemas/campaign.py.
// Kept as strings in the form (inputs are text); converted in buildPatch.
const optNumber = z
  .string()
  .trim()
  .refine((v) => v === '' || (Number.isFinite(Number(v)) && Number(v) >= 0 && Number(v) <= 3600), 'Número entre 0 y 3600')

export const toNum = (v: string): number | null => (v.trim() === '' ? null : Number(v))

const optUrl = z
  .string()
  .trim()
  .max(2048)
  .refine((v) => v === '' || /^https?:\/\//.test(v), 'Debe empezar por http:// o https://')

export const FORMATS = ['9:16', '1:1', '16:9', '4:5'] as const

export const campaignFormSchema = z
  .object({
    name: z.string().trim().min(1, 'Obligatorio').max(256, 'Máximo 256 caracteres'),
    source_url: optUrl,
    source_id: z.string().trim().max(256),
    source_instructions: z.string().max(20000),
    duration_min: optNumber,
    duration_max: optNumber,
    format: z.string().max(16),
    language: z.string().trim().max(16),
    captions_required: z.boolean(),
    watermark_url: optUrl,
    keywords: z.string(),
    exclude_keywords: z.string(),
  })
  .refine((v) => toNum(v.duration_min) == null || toNum(v.duration_max) == null || toNum(v.duration_min)! <= toNum(v.duration_max)!, {
    path: ['duration_max'],
    message: 'Debe ser ≥ duración mínima',
  })

export type CampaignFormValues = z.infer<typeof campaignFormSchema>

const splitList = (s: string) =>
  s
    .split(/[,\n]/)
    .map((x) => x.trim())
    .filter(Boolean)

export function formDefaults(c: CampaignDetailCampaign): CampaignFormValues {
  const spec = (c.spec ?? {}) as Record<string, unknown>
  const num = (v: unknown) => (typeof v === 'number' ? String(v) : '')
  return {
    name: c.name,
    source_url: c.source_url ?? '',
    source_id: c.source_id ?? '',
    source_instructions: c.source_instructions ?? '',
    duration_min: num(spec.duration_min),
    duration_max: num(spec.duration_max),
    format: typeof spec.format === 'string' ? spec.format : '',
    language: typeof spec.language === 'string' ? spec.language : '',
    captions_required: spec.captions_required === true,
    watermark_url: typeof spec.watermark_url === 'string' ? spec.watermark_url : '',
    keywords: Array.isArray(spec.keywords) ? spec.keywords.join(', ') : '',
    exclude_keywords: Array.isArray(spec.exclude_keywords) ? spec.exclude_keywords.join(', ') : '',
  }
}

const sameList = (a: unknown, b: string[]) => JSON.stringify(Array.isArray(a) ? a : []) === JSON.stringify(b)

/** Build a minimal PATCH body: only fields that actually changed. */
export function buildPatch(c: CampaignDetailCampaign, v: CampaignFormValues): CampaignUpdate {
  const out: CampaignUpdate = {}
  const spec = (c.spec ?? {}) as Record<string, unknown>
  const editableSource = c.source_provider === 'manual'
  if (v.name !== c.name) out.name = v.name
  if (editableSource && (v.source_url || null) !== (c.source_url ?? null)) out.source_url = v.source_url || null
  if (editableSource && (v.source_id || null) !== (c.source_id ?? null)) out.source_id = v.source_id || null
  if ((v.source_instructions || null) !== (c.source_instructions ?? null)) out.source_instructions = v.source_instructions || null

  const sp: CampaignSpecPatch = {}
  const dmin = toNum(v.duration_min)
  const dmax = toNum(v.duration_max)
  if (dmin !== (spec.duration_min ?? null)) sp.duration_min = dmin
  if (dmax !== (spec.duration_max ?? null)) sp.duration_max = dmax
  if ((v.format || null) !== (spec.format ?? null)) sp.format = v.format || null
  if ((v.language || null) !== (spec.language ?? null)) sp.language = v.language || null
  if (v.captions_required !== (spec.captions_required === true)) sp.captions_required = v.captions_required
  if ((v.watermark_url || null) !== (spec.watermark_url ?? null)) sp.watermark_url = v.watermark_url || null
  const kw = splitList(v.keywords)
  if (!sameList(spec.keywords, kw)) sp.keywords = kw
  const ex = splitList(v.exclude_keywords)
  if (!sameList(spec.exclude_keywords, ex)) sp.exclude_keywords = ex
  if (Object.keys(sp).length) out.spec = sp
  return out
}
