import { z } from 'zod'
import type { CandidateApproveOut, CandidateItem } from '@/api/types'

/** 65.4 → "1:05.4" (clip window inside the source video). */
export function fmtTimecode(sec: number): string {
  const s = Math.max(0, sec)
  const m = Math.floor(s / 60)
  const rest = s - m * 60
  return `${m}:${rest.toFixed(1).padStart(4, '0')}`
}

const YT_ID = /(?:youtube\.com\/(?:watch\?(?:.*&)?v=|shorts\/|embed\/|live\/)|youtu\.be\/)([A-Za-z0-9_-]{11})/

export function youtubeId(url?: string | null): string | null {
  if (!url) return null
  const m = YT_ID.exec(url)
  return m ? m[1] : null
}

/** Embed URL playing only the candidate window (YouTube sources only). */
export function previewEmbedUrl(url: string | null | undefined, start: number, end: number): string | null {
  const id = youtubeId(url)
  if (!id) return null
  const sp = new URLSearchParams({ start: String(Math.floor(start)), end: String(Math.ceil(end)), rel: '0' })
  return `https://www.youtube-nocookie.com/embed/${id}?${sp.toString()}`
}

/** Mirrors candidate_lifecycle: pending, or approved without a render job (retry). */
export function canApprove(c: Pick<CandidateItem, 'status' | 'render_job_id'>): boolean {
  return c.status === 'pending' || (c.status === 'approved' && !c.render_job_id)
}

/** Mirrors candidate_lifecycle.reject_candidate guards. */
export function canReject(c: Pick<CandidateItem, 'status' | 'render_job_id'>): boolean {
  return c.status === 'pending' || (c.status === 'approved' && !c.render_job_id)
}

export const rejectSchema = z.object({
  reason: z
    .string()
    .trim()
    .max(500, 'Máximo 500 caracteres')
    .transform((v) => v || undefined)
    .optional(),
})
export type RejectForm = z.input<typeof rejectSchema>

/** Toast copy for the approve result (the backend re-validates the rules). */
export function approveOutcome(r: CandidateApproveOut): { ok: boolean; title: string; description?: string } {
  if (r.status === 'approved') {
    return {
      ok: true,
      title: r.idempotent ? 'Ya estaba aprobado' : 'Candidato aprobado',
      description: r.render_job_id ? `Job de render ${r.render_job_id.slice(0, 8)} en cola del worker` : undefined,
    }
  }
  if (r.status === 'rejected') {
    return { ok: false, title: 'Rechazado por las reglas de la campaña', description: r.reason ?? undefined }
  }
  return { ok: false, title: 'No se pudo crear el job de render', description: r.reason ?? undefined }
}
